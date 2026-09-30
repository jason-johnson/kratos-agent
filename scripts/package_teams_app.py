#!/usr/bin/env python3
"""Build an environment-specific Microsoft Teams personal-tab app package."""

from __future__ import annotations

import argparse
import binascii
import json
import os
import struct
import subprocess
import uuid
import zlib
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "dist" / "kratos-teams-app.zip"


def _azd_value(name: str) -> str:
    result = subprocess.run(
        ["azd", "env", "get-value", name],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _required_app_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError("Set TEAMS_APP_ID or pass --app-id with a valid UUID") from exc


def _required_web_url(value: str) -> tuple[str, str]:
    url = value.rstrip("/")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("The Teams tab web URL must be an HTTPS origin without a path")
    return url, parsed.hostname


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))


def _icon_png(size: int, background: tuple[int, int, int, int]) -> bytes:
    pixels = [[background for _ in range(size)] for _ in range(size)]
    margin = max(2, size // 6)
    stroke = max(2, size // 9)
    white = (255, 255, 255, 255)

    for y in range(margin, size - margin):
        for x in range(margin, min(size - margin, margin + stroke)):
            pixels[y][x] = white

    center = size // 2
    for y in range(margin, size - margin):
        distance = abs(y - center)
        start = margin + stroke + max(0, distance - stroke // 2)
        for x in range(start, min(size - margin, start + stroke)):
            pixels[y][x] = white

    raw = b"".join(b"\x00" + b"".join(bytes(pixel) for pixel in row) for row in pixels)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _manifest(app_id: str, web_url: str, hostname: str) -> dict[str, object]:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.30/MicrosoftTeams.schema.json",
        "manifestVersion": "1.30",
        "version": "1.0.0",
        "id": app_id,
        "developer": {
            "name": "Kratos Agent",
            "websiteUrl": web_url,
            "privacyUrl": web_url,
            "termsOfUseUrl": web_url,
        },
        "icons": {"outline": "outline.png", "color": "color.png"},
        "name": {"short": "Kratos", "full": "Kratos Agent"},
        "description": {
            "short": "Run enterprise agent workflows in Microsoft Teams.",
            "full": "Use Kratos personas, grounded tools, citations, and generated files from a personal tab in Microsoft Teams.",
        },
        "accentColor": "#164E63",
        "staticTabs": [
            {
                "entityId": "kratos-agent",
                "name": "Kratos",
                "contentUrl": web_url,
                "websiteUrl": web_url,
                "scopes": ["personal"],
            }
        ],
        "permissions": ["identity"],
        "validDomains": [hostname],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-id",
        default=os.environ.get("TEAMS_APP_ID", "") or _azd_value("TEAMS_APP_ID"),
    )
    parser.add_argument(
        "--web-url",
        default=os.environ.get("KRATOS_WEB_URL", "") or _azd_value("AZURE_STATIC_WEB_APP_URL"),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        app_id = _required_app_id(args.app_id)
        web_url, hostname = _required_web_url(args.web_url)
    except ValueError as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "w", ZIP_DEFLATED) as package:
        package.writestr("manifest.json", json.dumps(_manifest(app_id, web_url, hostname), indent=2) + "\n")
        package.writestr("color.png", _icon_png(192, (8, 145, 178, 255)))
        package.writestr("outline.png", _icon_png(32, (0, 0, 0, 0)))

    print(f"Created Teams app package: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
