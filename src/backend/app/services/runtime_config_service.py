"""Versioned runtime configuration and cross-replica refresh."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.apm_service import ApmService
from app.services.blob_skill_service import BlobSkillService
from app.services.copilot_agent import CopilotAgent
from app.services.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)

_CONFIG_PREFIX = "runtime-config"
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024


class RuntimeConfigService:
    """Stores immutable configuration versions and refreshes peer replicas."""

    def __init__(
        self,
        settings: Settings,
        blob: BlobSkillService,
        registries: dict[str, SkillRegistry],
        copilot_agent: CopilotAgent,
        apm_service: ApmService,
    ) -> None:
        self._settings = settings
        self._blob = blob
        self._registries = registries
        self._agent = copilot_agent
        self._apm_service = apm_service
        self._current_versions: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def initialize(self, use_case: str) -> None:
        """Adopt the shared revision or create the first immutable baseline."""
        marker = await self._read_json(self._current_path(use_case))
        if marker:
            self._current_versions[use_case] = str(marker["version"])
            return
        await self.record_version(use_case, action="baseline", actor="system", reset_sessions=False)

    async def watch(self, use_case: str, interval_seconds: float = 10.0) -> None:
        """Poll the shared revision marker and reload this replica on change."""
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.refresh_if_changed(use_case)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Runtime config refresh failed for '%s'", use_case)

    async def refresh_if_changed(self, use_case: str) -> bool:
        """Reload one replica when the shared current-version marker changed."""
        marker = await self._read_json(self._current_path(use_case))
        if not marker:
            return False
        version = str(marker["version"])
        if self._current_versions.get(use_case) == version:
            return False
        await self._reload(use_case)
        self._current_versions[use_case] = version
        logger.info("Runtime config refreshed for '%s' at version %s", use_case, version)
        return True

    async def record_version(
        self,
        use_case: str,
        *,
        action: str,
        actor: str,
        reset_sessions: bool = True,
    ) -> dict[str, Any]:
        """Snapshot the current configuration, publish it, and audit the mutation."""
        async with self._lock:
            created_at = datetime.now(UTC)
            version = f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
            revision = {
                "version": version,
                "useCase": use_case,
                "createdAt": created_at.isoformat(),
                "actor": actor,
                "action": action,
                "files": self._capture_files(use_case),
            }
            await self._write_json(self._version_path(use_case, version), revision)
            marker = {key: revision[key] for key in ("version", "useCase", "createdAt", "actor", "action")}
            await self._write_json(self._current_path(use_case), marker)
            self._current_versions[use_case] = version
            if reset_sessions:
                await self._agent.reset_sessions_for_use_case(use_case)
            logger.info(
                "Runtime config version %s recorded for '%s' action=%s actor=%s",
                version,
                use_case,
                action,
                actor,
            )
            return marker

    async def list_versions(self, use_case: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return newest immutable version metadata without snapshot payloads."""
        names = await self._list_version_names(use_case)
        versions: list[dict[str, Any]] = []
        for name in sorted(names, reverse=True)[:limit]:
            revision = await self._read_json(name)
            if revision:
                versions.append({key: revision[key] for key in ("version", "useCase", "createdAt", "actor", "action")})
        return versions

    async def rollback(self, use_case: str, version: str, actor: str) -> dict[str, Any]:
        """Restore a prior immutable snapshot and publish rollback as a new version."""
        if not re.fullmatch(r"\d{8}T\d{12}Z-[0-9a-f]{8}", version):
            raise ValueError("Invalid runtime config version")
        async with self._lock:
            revision = await self._read_json(self._version_path(use_case, version))
            if not revision:
                raise FileNotFoundError(version)
            files = revision.get("files")
            if not isinstance(files, dict):
                raise ValueError(f"Version '{version}' has no valid file snapshot")
            await self._restore_files(use_case, files)
            await self._reload(use_case)

        return await self.record_version(
            use_case,
            action=f"rollback:{version}",
            actor=actor,
        )

    def _capture_files(self, use_case: str) -> dict[str, str]:
        root = self._config_root(use_case)
        candidates = [root / "SYSTEM_PROMPT.md", root / ".mcp.json"]
        skills = root / "skills"
        if skills.is_dir():
            candidates.extend(path for path in skills.rglob("*") if path.is_file())

        files: dict[str, str] = {}
        total = 0
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                continue
            content = path.read_bytes()
            if len(content) > _MAX_FILE_BYTES:
                raise ValueError(f"Runtime config file exceeds {_MAX_FILE_BYTES} bytes: {path.name}")
            total += len(content)
            if total > _MAX_SNAPSHOT_BYTES:
                raise ValueError(f"Runtime config snapshot exceeds {_MAX_SNAPSHOT_BYTES} bytes")
            files[path.relative_to(root).as_posix()] = base64.b64encode(content).decode()
        return files

    async def _restore_files(self, use_case: str, files: dict[str, str]) -> None:
        root = self._config_root(use_case)
        skill_root = root / "skills"
        if skill_root.is_dir():
            shutil.rmtree(skill_root)
        skill_root.mkdir(parents=True, exist_ok=True)

        if self._blob.is_available:
            for skill_name in await self._blob.list_skill_names(use_case):
                await self._blob.delete_skill(use_case, skill_name)

        for relative, encoded in files.items():
            if relative not in {"SYSTEM_PROMPT.md", ".mcp.json"} and not relative.startswith("skills/"):
                raise ValueError(f"Invalid runtime config path in version: {relative}")
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            content = base64.b64decode(encoded, validate=True)
            path.write_bytes(content)
            if self._blob.is_available:
                await self._blob.upload_file(f"use-cases/{use_case}/{relative}", content)

    async def _reload(self, use_case: str) -> None:
        registry = self._registries.get(use_case)
        if registry is None:
            raise ValueError(f"Use-case '{use_case}' is not loaded")
        await registry.load(
            use_case,
            self._blob,
            apm_service=self._apm_service,
            local_root=self._settings.apm_use_cases_root,
        )
        await self._agent.reset_sessions_for_use_case(use_case)

    def _config_root(self, use_case: str) -> Path:
        if self._blob.is_available:
            return self._blob.local_dir(use_case)
        return Path(self._settings.apm_use_cases_root) / use_case

    def _local_history_root(self, use_case: str) -> Path:
        return Path(self._settings.local_data_dir) / "runtime-config" / use_case

    @staticmethod
    def _current_path(use_case: str) -> str:
        return f"{_CONFIG_PREFIX}/{use_case}/current.json"

    @staticmethod
    def _version_path(use_case: str, version: str) -> str:
        return f"{_CONFIG_PREFIX}/{use_case}/versions/{version}.json"

    async def _write_json(self, path: str, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, sort_keys=True).encode()
        if self._blob.is_available:
            await self._blob.upload_file(path, content)
            return
        local = Path(self._settings.local_data_dir) / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content)

    async def _read_json(self, path: str) -> dict[str, Any] | None:
        if self._blob.is_available:
            content = await self._blob.download_file(path)
        else:
            local = Path(self._settings.local_data_dir) / path
            content = local.read_bytes() if local.is_file() else None
        return json.loads(content) if content else None

    async def _list_version_names(self, use_case: str) -> list[str]:
        prefix = f"{_CONFIG_PREFIX}/{use_case}/versions/"
        if self._blob.is_available:
            return await self._blob.list_files(prefix)
        root = self._local_history_root(use_case) / "versions"
        return [str(path.relative_to(Path(self._settings.local_data_dir))) for path in root.glob("*.json")]
