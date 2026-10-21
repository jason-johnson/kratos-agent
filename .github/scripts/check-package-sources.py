from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_HOSTS = {
    "files.pythonhosted.org",
    "pypi.org",
    "registry.npmjs.org",
}
ALLOWED_NPM_HOST_SUFFIXES = (
    ".pkgs.dev.azure.com",
    ".pkgs.visualstudio.com",
)


def iter_resolved_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "resolved" and isinstance(child, str) and child.startswith(("http://", "https://")):
                urls.append(child)
            else:
                urls.extend(iter_resolved_urls(child))
    elif isinstance(value, list):
        for child in value:
            urls.extend(iter_resolved_urls(child))
    return urls


def main() -> None:
    errors: list[str] = []
    uv_lock = ROOT / "src/backend/uv.lock"
    uv_text = uv_lock.read_text(encoding="utf-8")

    for host in FORBIDDEN_HOSTS:
        if host in uv_text:
            errors.append(f"{uv_lock.relative_to(ROOT)} contains forbidden host {host}")
    if "pkgs.dev.azure.com" not in uv_text:
        errors.append(f"{uv_lock.relative_to(ROOT)} does not use Azure Artifacts")

    for relative_path in ("src/frontend/package-lock.json", "mocks/package-lock.json"):
        path = ROOT / relative_path
        lock = json.loads(path.read_text(encoding="utf-8"))
        for url in iter_resolved_urls(lock):
            host = (urlparse(url).hostname or "").lower()
            if host in FORBIDDEN_HOSTS:
                errors.append(f"{relative_path} contains forbidden host {host}")
            elif not host.endswith(ALLOWED_NPM_HOST_SUFFIXES):
                errors.append(f"{relative_path} contains unapproved package host {host}")

    if errors:
        raise SystemExit("\n".join(errors))

    print("Package locks use only approved private feeds.")


if __name__ == "__main__":
    main()
