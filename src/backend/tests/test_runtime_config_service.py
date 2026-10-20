"""Tests for runtime config versioning, rollback, and replica refresh."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.services.runtime_config_service import RuntimeConfigService
from app.services.skill_registry import SkillRegistry

USE_CASE = "agent-reliability-engineer"


def _write_use_case(root):
    use_case = root / USE_CASE
    skill = use_case / "skills" / "investigate"
    skill.mkdir(parents=True)
    (use_case / "SYSTEM_PROMPT.md").write_text("original prompt")
    (use_case / ".mcp.json").write_text("{}")
    (skill / "SKILL.md").write_text(
        "---\nname: investigate\ndescription: Investigate\nenabled: true\n---\nOriginal instructions"
    )
    return use_case


async def _service(tmp_path):
    use_cases = tmp_path / "use-cases"
    use_case = _write_use_case(use_cases)
    settings = Settings(
        local_mode=True,
        apm_use_cases_root=str(use_cases),
        local_data_dir=str(tmp_path / "data"),
    )
    blob = MagicMock()
    blob.is_available = False
    registry = SkillRegistry()
    await registry.load(USE_CASE, local_root=str(use_cases))
    agent = AsyncMock()
    apm = AsyncMock()
    apm.list_materialised_skill_dirs.return_value = []
    apm.list_mcp_servers.return_value = []
    service = RuntimeConfigService(
        settings,
        blob,
        {USE_CASE: registry},
        agent,
        apm,
    )
    return service, registry, agent, use_case, settings


@pytest.mark.asyncio
async def test_versions_are_audited_and_rollback_restores_snapshot(tmp_path):
    service, registry, agent, use_case, _ = await _service(tmp_path)
    await service.initialize(USE_CASE)
    baseline = (await service.list_versions(USE_CASE))[0]

    (use_case / "SYSTEM_PROMPT.md").write_text("changed prompt")
    extra = use_case / "skills" / "temporary"
    extra.mkdir()
    (extra / "SKILL.md").write_text("---\nname: temporary\nenabled: true\n---\nTemporary")
    changed = await service.record_version(USE_CASE, action="test.change", actor="operator")

    versions = await service.list_versions(USE_CASE)
    assert [version["action"] for version in versions] == ["test.change", "baseline"]
    assert changed["actor"] == "operator"

    rollback = await service.rollback(USE_CASE, baseline["version"], "operator")

    assert rollback["action"] == f"rollback:{baseline['version']}"
    assert (use_case / "SYSTEM_PROMPT.md").read_text() == "original prompt"
    assert not extra.exists()
    assert "temporary" not in registry.skills
    assert agent.reset_sessions_for_use_case.await_count >= 2


@pytest.mark.asyncio
async def test_peer_replica_refreshes_when_shared_marker_changes(tmp_path):
    service_a, _, _, use_case, settings = await _service(tmp_path)
    await service_a.initialize(USE_CASE)

    registry_b = SkillRegistry()
    await registry_b.load(USE_CASE, local_root=settings.apm_use_cases_root)
    agent_b = AsyncMock()
    blob_b = MagicMock()
    blob_b.is_available = False
    apm_b = AsyncMock()
    apm_b.list_materialised_skill_dirs.return_value = []
    apm_b.list_mcp_servers.return_value = []
    service_b = RuntimeConfigService(settings, blob_b, {USE_CASE: registry_b}, agent_b, apm_b)
    await service_b.initialize(USE_CASE)

    (use_case / "SYSTEM_PROMPT.md").write_text("peer-visible prompt")
    await service_a.record_version(USE_CASE, action="system-prompt.update", actor="operator")

    assert await service_b.refresh_if_changed(USE_CASE) is True
    assert registry_b.system_prompt == "peer-visible prompt"
    agent_b.reset_sessions_for_use_case.assert_awaited_once_with(USE_CASE)
    assert await service_b.refresh_if_changed(USE_CASE) is False
