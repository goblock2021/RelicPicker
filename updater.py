"""
Check for updates via GitHub Releases API.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Optional

log = logging.getLogger("relicpicker.updater")

GITHUB_API = "https://api.github.com/repos/goblock2021/RelicPicker/releases/latest"


def _parse_version(tag: str) -> tuple[int, ...]:
    """Parse a version tag like 'v1.2.3' or '1.2.3' into a comparable tuple."""
    tag = tag.lstrip("v")
    parts = tag.split(".")
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            result.append(0)
    # Pad to at least 3 segments
    while len(result) < 3:
        result.append(0)
    return tuple(result)


def _parse_current_version() -> tuple[int, ...]:
    from version import __version__
    return _parse_version(__version__)


def check_for_update(timeout: float = 5.0) -> Optional[dict]:
    """Check GitHub for a newer release.

    Returns None if up-to-date, no network, or API error.
    Returns a dict with update info if a newer version is available:
        {version, url, download_url, body}
    """
    try:
        req = urllib.request.Request(
            GITHUB_API,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "RelicPicker-UpdateCheck/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        log.debug("Update check failed (network/parse): %s", e)
        return None

    remote_tag = data.get("tag_name", "")
    if not remote_tag:
        return None

    remote_version = _parse_version(remote_tag)
    current_version = _parse_current_version()

    if remote_version <= current_version:
        log.debug("Up to date: local=%s, remote=%s", current_version, remote_version)
        return None

    download_url = data.get("html_url", "")
    # Try to get direct EXE download URL from assets
    exe_url = ""
    for asset in data.get("assets", []):
        if asset.get("name", "").endswith(".exe"):
            exe_url = asset.get("browser_download_url", "")
            break

    log.info("Update available: %s -> %s", current_version, remote_tag)

    return {
        "version": remote_tag,
        "url": download_url,
        "download_url": exe_url or download_url,
        "body": data.get("body", ""),
    }
