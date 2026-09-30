import importlib.util
import json
import struct
import sys
import uuid
from pathlib import Path
from zipfile import ZipFile

SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "package_teams_app.py"
SPEC = importlib.util.spec_from_file_location("package_teams_app", SCRIPT_PATH)
assert SPEC and SPEC.loader
package_teams_app = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = package_teams_app
SPEC.loader.exec_module(package_teams_app)


def _png_dimensions(content: bytes) -> tuple[int, int]:
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    return struct.unpack(">II", content[16:24])


def test_builds_parameterized_teams_package(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "kratos-teams-app.zip"
    app_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "kratos.example.invalid"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT_PATH),
            "--app-id",
            app_id,
            "--web-url",
            "https://kratos.example.invalid",
            "--output",
            str(output),
        ],
    )

    assert package_teams_app.main() == 0

    with ZipFile(output) as package:
        assert set(package.namelist()) == {"manifest.json", "color.png", "outline.png"}
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["id"] == app_id
        assert manifest["staticTabs"][0]["contentUrl"] == "https://kratos.example.invalid"
        assert manifest["validDomains"] == ["kratos.example.invalid"]
        assert _png_dimensions(package.read("color.png")) == (192, 192)
        assert _png_dimensions(package.read("outline.png")) == (32, 32)
