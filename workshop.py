"""
Creative Workshop (创意工坊) — GitHub API client.
All workshop operations: browse, share, delete.
Uses GitHub Issues as submission channel (no fork/PR needed).
Uses only stdlib (no extra dependencies).
"""

import base64
import json
import logging
import time
import uuid as uuid_module
import urllib.error
import urllib.request
from typing import Optional

log = logging.getLogger("relicpicker.workshop")

# ── Configuration ──────────────────────────────────────────────────
WORKSHOP_REPO_OWNER = "goblock2021"
WORKSHOP_REPO_NAME = "RelicPickerWorkshop"
WORKSHOP_API_BASE = f"https://api.github.com/repos/{WORKSHOP_REPO_OWNER}/{WORKSHOP_REPO_NAME}"
GITHUB_API = "https://api.github.com"
OAUTH_CLIENT_ID = "Ov23lidemWuGVcopxhSS"
SUBMISSIONS_DIR = "submissions"
CACHE_TTL = 300  # seconds

# Issue markers — Actions validator uses these to identify workshop Issues
MARKER_SHARE = "[RELICPICKER_SHARE]"
MARKER_DELETE = "[RELICPICKER_DELETE]"

# ── Cache ──────────────────────────────────────────────────────────
_cache: dict = {"data": [], "fetched_at": 0.0}


# ── Errors ─────────────────────────────────────────────────────────

class WorkshopError(Exception):
    """Base error for workshop operations."""
    pass


class WorkshopAuthError(WorkshopError):
    """Raised when token is missing or invalid."""
    pass


class WorkshopRateLimitError(WorkshopError):
    """Raised when GitHub rate limit is hit."""
    pass


# ── HTTP Helpers ───────────────────────────────────────────────────

def _api_request(
    url: str,
    method: str = "GET",
    token: Optional[str] = None,
    body: Optional[dict] = None,
    accept: str = "application/vnd.github+json",
    timeout: float = 15.0,
) -> dict:
    """Core HTTP helper for GitHub API. Returns parsed JSON response.

    Raises WorkshopError subclasses on failure.
    """
    headers = {
        "Accept": accept,
        "User-Agent": "RelicPicker-Workshop/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        status = e.code
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")[:500]
        except Exception:
            pass

        if status == 401:
            raise WorkshopAuthError("GitHub Token 无效或已过期")
        elif status == 403:
            if "rate limit" in error_body.lower() or "secondary rate limit" in error_body.lower():
                raise WorkshopRateLimitError("GitHub API 请求过于频繁，请稍后再试")
            raise WorkshopAuthError(f"访问被拒绝 (403): {error_body[:200]}")
        elif status == 404:
            raise WorkshopError(f"资源不存在 (404): {url}")
        elif status == 422:
            raise WorkshopError(f"请求无效 (422): {error_body[:300]}")
        elif status == 410:
            raise WorkshopError("Issues 功能未在此仓库启用")
        else:
            raise WorkshopError(f"GitHub API 错误 ({status}): {error_body[:200]}")
    except urllib.error.URLError as e:
        raise WorkshopError(f"网络错误，无法连接 GitHub: {e.reason}")
    except (OSError, ValueError) as e:
        raise WorkshopError(f"请求失败: {e}")


def _github_get(path: str, token: Optional[str] = None) -> dict | list:
    """GET from GitHub API."""
    url = f"{GITHUB_API}{path}" if path.startswith("/") else path
    if not url.startswith("http"):
        url = f"{GITHUB_API}{url}"
    return _api_request(url, method="GET", token=token)


def _github_post(path: str, token: str, body: dict) -> dict:
    """POST to GitHub API (authenticated)."""
    return _api_request(f"{GITHUB_API}{path}", method="POST", token=token, body=body)


# ── Token ──────────────────────────────────────────────────────────

def validate_token(token: str) -> str:
    """Validate a GitHub PAT and return the authenticated username.

    Raises WorkshopAuthError if token is invalid.
    """
    try:
        user = _github_get("/user", token=token)
        username = user.get("login", "")
        if not username:
            raise WorkshopAuthError("Token 有效但无法获取用户名")
        log.info("Token validated for user: %s", username)
        return username
    except WorkshopAuthError:
        raise
    except WorkshopError as e:
        raise WorkshopAuthError(f"Token 验证失败: {e}")


# ── OAuth Device Flow ──────────────────────────────────────────────

def start_device_flow() -> dict:
    """Initiate GitHub Device Flow.

    Returns:
        {device_code, user_code, verification_uri, interval}
    """
    body = {
        "client_id": OAUTH_CLIENT_ID,
        "scope": "public_repo",
    }
    result = _api_request(
        "https://github.com/login/device/code",
        method="POST",
        body=body,
        accept="application/json",
        timeout=30.0,
    )
    log.info("Device flow started: user_code=%s", result.get("user_code"))
    return {
        "device_code": result.get("device_code", ""),
        "user_code": result.get("user_code", ""),
        "verification_uri": result.get("verification_uri", "https://github.com/login/device"),
        "interval": int(result.get("interval", 5)),
    }


def poll_device_token(device_code: str) -> dict:
    """Poll for access token during Device Flow.

    Returns:
        {access_token} on success
        {error: "authorization_pending"} if user hasn't confirmed yet
        {error: "expired_token"} if timed out

    Raises WorkshopAuthError on other failures.
    """
    body = {
        "client_id": OAUTH_CLIENT_ID,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    try:
        result = _api_request(
            "https://github.com/login/oauth/access_token",
            method="POST",
            body=body,
            accept="application/json",
        )
    except WorkshopError:
        # 4xx responses during polling are expected
        return {"error": "authorization_pending"}

    if "access_token" in result:
        log.info("Device flow completed — got token")
        return {"access_token": result["access_token"]}

    error = result.get("error", "unknown")
    if error == "authorization_pending":
        return {"error": "authorization_pending"}
    elif error in ("expired_token", "access_denied"):
        return {"error": error}
    else:
        raise WorkshopAuthError(f"Token 获取失败: {error}")


# ── Allowed user fields (must match process_issue.py) ──────────────
ALLOWED_FIELDS = [
    "title", "description", "effects", "shop", "color",
    "relic_id", "effect_names", "curse_names", "relic_name",
]


# ── Issue Operations ───────────────────────────────────────────────

def _create_issue(token: str, title: str, body: str) -> dict:
    """Create a GitHub Issue in the workshop repo.

    Returns {issue_url, issue_number}.
    """
    result = _github_post(
        f"/repos/{WORKSHOP_REPO_OWNER}/{WORKSHOP_REPO_NAME}/issues",
        token=token,
        body={"title": title, "body": body},
    )
    url = result.get("html_url", "")
    number = result.get("number", 0)
    log.info("Created Issue #%d: %s", number, url)
    return {"issue_url": url, "issue_number": number}


# ── Workshop Operations ────────────────────────────────────────────

def share_submission(token: str, data: dict) -> dict:
    """Share a relic config to the workshop via GitHub Issue.

    Only business fields are sent — id/author/created_at are generated
    server-side by the Actions validator.

    Flow:
    1. Validate token → get username
    2. Strip to allowed fields only
    3. Create Issue with MARKER_SHARE + JSON body
    4. Actions bot validates and commits the file

    Args:
        token: GitHub PAT
        data: dict with user fields (extra/forged fields are stripped)

    Returns:
        {issue_url, issue_number}
    """
    # 1) Validate token
    username = validate_token(token)

    # 2) Strip to allowed fields only
    user_data = {k: data[k] for k in ALLOWED_FIELDS if k in data}

    json_str = json.dumps(user_data, ensure_ascii=False, indent=2)

    # 3) Create Issue
    issue_title = user_data.get("title", "").strip() or "未命名配置"
    desc = user_data.get("description", "").strip()

    body = f"{MARKER_SHARE}\n"
    body += f"**{issue_title}**\n\n"
    if desc:
        body += f"{desc}\n\n"
    body += f"```json\n{json_str}\n```\n"
    body += f"\n> 🤖 由 RelicPicker 创意工坊自动提交 — by **{username}**"

    issue_result = _create_issue(token, f"[分享] {issue_title}", body)

    # Invalidate cache
    _cache["fetched_at"] = 0.0

    return issue_result


def delete_submission(token: str, submission_id: str) -> dict:
    """Delete own submission from the workshop via GitHub Issue.

    Flow:
    1. Validate token → get username
    2. Create Issue with MARKER_DELETE + submission_id
    3. Actions bot verifies author and deletes the file

    No fork/PR required.

    Returns:
        {issue_url, issue_number}
    """
    # 1) Validate token
    username = validate_token(token)

    # 2) Create Issue
    body = f"{MARKER_DELETE}\n"
    body += f"submission_id: `{submission_id}`\n"
    body += f"\n> 🤖 由 RelicPicker 创意工坊自动提交 — by **{username}**"

    issue_result = _create_issue(
        token,
        f"[删除] {submission_id[:8]}",
        body,
    )

    # Invalidate cache
    _cache["fetched_at"] = 0.0

    return issue_result


# ── Read Operations ────────────────────────────────────────────────

def _get_file_info(file_path: str, ref: str = "main", token: Optional[str] = None) -> dict:
    """Get file content, SHA, and metadata from the repo."""
    result = _github_get(
        f"/repos/{WORKSHOP_REPO_OWNER}/{WORKSHOP_REPO_NAME}/contents/{file_path}?ref={ref}",
        token=token,
    )
    return result


def _list_workshop_files(token: Optional[str] = None) -> list[dict]:
    """List all files in the submissions directory."""
    result = _github_get(
        f"/repos/{WORKSHOP_REPO_OWNER}/{WORKSHOP_REPO_NAME}/contents/{SUBMISSIONS_DIR}",
        token=token,
    )
    if isinstance(result, list):
        return [f for f in result if f.get("name", "").endswith(".json")]
    elif isinstance(result, dict) and result.get("type") == "file":
        return [result] if result.get("name", "").endswith(".json") else []
    return []


def fetch_all_submissions(token: Optional[str] = None) -> list[dict]:
    """Fetch all workshop submissions with caching.

    Anonymous browsing (no token) works for public repos.
    Cache TTL: 300 seconds (5 minutes).

    Returns list of submission dicts, each with parsed JSON content.
    """
    now = time.time()
    if _cache["data"] and (now - _cache["fetched_at"]) < CACHE_TTL:
        log.debug("Workshop cache hit (%d items, age=%.0fs)",
                  len(_cache["data"]), now - _cache["fetched_at"])
        return _cache["data"]

    log.info("Fetching workshop submissions...")
    files = _list_workshop_files(token=token)

    submissions = []
    fetch_errors = 0

    for f in files:
        name = f.get("name", "")
        try:
            # Try using content from listing (may include base64 content)
            content_b64 = f.get("content", "")
            if content_b64 and len(content_b64) > 10:
                content_str = base64.b64decode(content_b64).decode("utf-8")
            else:
                # Fetch individually
                file_info = _get_file_info(
                    f"{SUBMISSIONS_DIR}/{name}",
                    ref="main",
                    token=token,
                )
                content_str = base64.b64decode(file_info.get("content", "")).decode("utf-8")

            raw = json.loads(content_str)

            # Flatten: merge data.* + top-level management fields
            # Format: {id, author, created_at, version, issue_number, data: {...user fields...}}
            inner = raw.get("data", raw)  # tolerate legacy flat format
            submission = {
                "id": raw.get("id", ""),
                "author": raw.get("author", ""),
                "created_at": raw.get("created_at", ""),
                "version": raw.get("version", 1),
                "issue_number": raw.get("issue_number", 0),
                "title": inner.get("title", ""),
                "description": inner.get("description", ""),
                "effects": inner.get("effects", []),
                "shop": inner.get("shop", ""),
                "color": inner.get("color", 0),
                "relic_id": inner.get("relic_id", 0),
                "effect_names": inner.get("effect_names", []),
                "curse_names": inner.get("curse_names", []),
                "relic_name": inner.get("relic_name", ""),
                "_download_url": f.get("download_url", ""),
            }
            submissions.append(submission)
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
            log.warning("Failed to parse submission %s: %s", name, e)
            fetch_errors += 1
        except WorkshopError as e:
            log.warning("Failed to fetch submission %s: %s", name, e)
            fetch_errors += 1

    # Sort by created_at descending (newest first)
    submissions.sort(key=lambda s: s.get("created_at", ""), reverse=True)

    _cache["data"] = submissions
    _cache["fetched_at"] = now

    log.info("Fetched %d submissions (%d errors)", len(submissions), fetch_errors)
    return submissions


def invalidate_cache():
    """Force refresh on next fetch_all_submissions call."""
    _cache["fetched_at"] = 0.0
