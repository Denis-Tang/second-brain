"""Version check and verified staging for the public GitHub release feed.

Checking never installs or executes anything: it only asks GitHub for the newest published
release. Downloading is a separate, explicit user action that streams the release ZIP into
the application data directory, verifies its SHA256 against the published asset digest and
checks the archive layout — it never touches the running installation. The network is only
touched on an explicit user action, results are cached, and every failure degrades to a
short message instead of an exception.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import __version__

REPOSITORY = "Denis-Tang/second-brain"
RELEASE_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CACHE_NAME = "update-check.json"
# Anonymous GitHub API calls are limited per exit IP, so a result is reused instead of
# re-asked on every click; a forced check still waits REPEAT_SECONDS to avoid hammering.
CACHE_SECONDS = 6 * 3600
REPEAT_SECONDS = 60
ASSET_SUFFIX = "-windows-x64.zip"
NOTES_LIMIT = 4000
UPDATES_FOLDER = "updates"
RECORD_NAME = "download.json"
CHUNK_BYTES = 1 << 16
FREE_SPACE_MARGIN = 64 * 1024 * 1024
PACKAGE_ROOT = "shared-brain"
REQUIRED_FILES = (
    f"{PACKAGE_ROOT}/shared-brain.exe",
    f"{PACKAGE_ROOT}/desktop/SharedBrain.Desktop.exe",
    f"{PACKAGE_ROOT}/runtime/node.exe",
    f"{PACKAGE_ROOT}/integrations/README.md",
)


class UpdateError(RuntimeError):
    """A short message safe to display without exposing provider details."""


def parse_version(text: str) -> tuple:
    """Sort key for a release tag: a pre-release sorts before the same numbers without one."""
    value = str(text or "").strip().lstrip("vV")
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:[-+](.*))?", value)
    if not match:
        return ()
    numbers = tuple(int(part) for part in match.group(1).split("."))
    return numbers, 0 if match.group(2) else 1


def is_newer(candidate: str, current: str) -> bool:
    """Compare numerically so 0.4.10 is newer than 0.4.9."""
    remote, local = parse_version(candidate), parse_version(current)
    if not remote or not local:
        return False
    return remote > local


def running_version() -> str:
    return __version__


def release_page() -> str:
    return RELEASE_PAGE


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rate_limit_message(response) -> str:
    reset = response.headers.get("x-ratelimit-reset", "")
    if reset.isdigit():
        moment = datetime.fromtimestamp(int(reset), tz=timezone.utc).astimezone()
        return f"GitHub 接口查询次数已用完，请在 {moment:%H:%M} 之后再试。"
    return "GitHub 接口暂时不可用（查询次数受限），请稍后再试。"


def _select_asset(payload: dict, version: str) -> dict:
    assets = [asset for asset in payload.get("assets") or [] if isinstance(asset, dict)]
    named = [asset for asset in assets if str(asset.get("name") or "").endswith(ASSET_SUFFIX)]
    preferred = [asset for asset in named if f"-{version}-" in str(asset.get("name") or "")]
    for asset in preferred + named:
        digest = str(asset.get("digest") or "")
        return {
            "name": str(asset.get("name") or ""),
            "url": str(asset.get("browser_download_url") or ""),
            "size": int(asset.get("size") or 0),
            "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else "",
        }
    return {}


def fetch_latest(timeout: float = 8.0) -> dict:
    """Ask GitHub for the newest published release, raising UpdateError on any failure."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"shared-brain/{__version__}",
    }
    try:
        response = httpx.get(LATEST_RELEASE_API, headers=headers, timeout=timeout, follow_redirects=True)
    except (httpx.HTTPError, httpx.InvalidURL, OSError):
        # httpx.InvalidURL is not an HTTPError and is also raised for a malformed proxy setting,
        # which would otherwise escape the "never raises" contract of check_latest.
        raise UpdateError("无法连接 GitHub，请检查网络后重试。") from None
    if response.status_code in {403, 429}:
        raise UpdateError(_rate_limit_message(response))
    if response.status_code == 404:
        raise UpdateError("没有找到已发布的版本记录。")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise UpdateError(f"GitHub 返回 HTTP {response.status_code}。") from None
    try:
        payload = response.json()
    except ValueError:
        raise UpdateError("GitHub 返回的内容不是有效 JSON。") from None
    if not isinstance(payload, dict):
        raise UpdateError("GitHub 返回的发布信息格式不正确。")
    tag = str(payload.get("tag_name") or "").strip()
    version = tag.lstrip("vV")
    if not version:
        raise UpdateError("发布信息缺少版本号。")
    notes = str(payload.get("body") or "").strip()
    return {
        "tag": tag,
        "version": version,
        "published_at": str(payload.get("published_at") or payload.get("created_at") or ""),
        "notes": notes[:NOTES_LIMIT],
        "notes_truncated": len(notes) > NOTES_LIMIT,
        "page": RELEASE_PAGE,
        "asset": _select_asset(payload, version),
    }


def _cache_path(home: Path) -> Path:
    return Path(home) / CACHE_NAME


def _read_cache(home: Path) -> dict:
    try:
        payload = json.loads(_cache_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cache(home: Path, release: dict, moment: datetime) -> None:
    path = _cache_path(home)
    text = json.dumps({"checked_at": moment.isoformat(), "release": release}, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _age_seconds(checked_at, moment: datetime):
    try:
        recorded = datetime.fromisoformat(str(checked_at))
    except ValueError:
        return None
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - recorded).total_seconds())


def _result(release: dict | None, moment: datetime, checked_at: str = "", cached: bool = False, failure: str = "") -> dict:
    release = release or {}
    version = str(release.get("version") or "")
    available = bool(version)
    newer = available and is_newer(version, __version__)
    if failure:
        message = failure
    elif newer:
        message = f"发现新版本 v{version}。"
    elif available:
        message = f"已是最新版本 v{__version__}。"
    else:
        message = "暂时无法确认最新版本。"
    return {
        "current": __version__,
        "latest": version,
        "newer": newer,
        "available": available,
        "stale": bool(failure and available),
        "published_at": str(release.get("published_at") or ""),
        "notes": str(release.get("notes") or ""),
        "notes_truncated": bool(release.get("notes_truncated")),
        "asset": release.get("asset") or {},
        "page": RELEASE_PAGE,
        "cached": cached,
        "checked_at": checked_at or moment.isoformat(),
        "message": message,
    }


def check_latest(home: Path, force: bool = False, timeout: float = 8.0, fetcher=None) -> dict:
    """Return the newest known release state without raising on network problems."""
    moment = _now()
    cache = _read_cache(home)
    release = cache.get("release") if isinstance(cache.get("release"), dict) else {}
    checked_at = str(cache.get("checked_at") or "")
    age = _age_seconds(checked_at, moment) if checked_at else None
    if age is not None and (age < REPEAT_SECONDS or (age < CACHE_SECONDS and not force)):
        return _result(release, moment, checked_at=checked_at, cached=True)
    try:
        fresh = (fetcher or fetch_latest)(timeout)
    except UpdateError as exc:
        if release:
            return _result(release, moment, checked_at=checked_at, cached=True, failure=str(exc))
        return _result(None, moment, failure=str(exc))
    _write_cache(home, fresh, moment)
    return _result(fresh, moment, checked_at=moment.isoformat())


def updates_directory(home: Path) -> Path:
    return Path(home) / UPDATES_FOLDER


def staged_archive(home: Path, version: str, name: str = "") -> Path:
    """Where a verified release archive is kept before any replacement happens."""
    safe = Path(name).name if name else f"shared-brain-{version}-windows-x64.zip"
    return updates_directory(home) / str(version) / safe


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_archive(path: Path) -> dict:
    """Check the release layout without extracting anything to disk."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        raise UpdateError("下载的压缩包无法读取，可能不完整，请重试。") from None
    if not names:
        raise UpdateError("下载的压缩包是空的。")
    if {name.split("/", 1)[0] for name in names} != {PACKAGE_ROOT}:
        raise UpdateError("压缩包结构与发布的包不一致，已停止使用。")
    for required in REQUIRED_FILES:
        if required not in names:
            raise UpdateError("压缩包缺少必要文件：" + required)
    if not any(name.startswith(f"{PACKAGE_ROOT}/_internal/") for name in names):
        raise UpdateError(f"压缩包缺少 {PACKAGE_ROOT}/_internal 运行时目录。")
    return {"entries": len(names)}


def _read_record(folder: Path) -> dict:
    try:
        payload = json.loads((folder / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_record(folder: Path, record: dict) -> None:
    path = folder / RECORD_NAME
    temporary = path.with_name(path.name + ".tmp")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def verified_archive(home: Path, version: str) -> dict:
    """A previously verified archive for this version; the digest is re-checked before use."""
    if not version:
        return {}
    folder = updates_directory(home) / str(version)
    record = _read_record(folder)
    name = Path(str(record.get("name") or "")).name
    if record.get("version") != str(version) or not name:
        return {}
    path = folder / name
    if not path.is_file():
        return {}
    size = record.get("size")
    if isinstance(size, int) and size > 0 and path.stat().st_size != size:
        return {}
    return {**record, "path": str(path)}


def _open_download(url: str, timeout: float):
    return httpx.stream("GET", url, follow_redirects=True, timeout=timeout,
                        headers={"User-Agent": f"shared-brain/{__version__}"})


def download_release(home: Path, release: dict, progress=None, cancelled=None, stream=None,
                     timeout: float = 60.0) -> dict:
    """Stream the release archive into the application data directory and verify it.

    The running installation is never read, moved or replaced here: a verified archive only
    waits in ``<home>/updates/<version>/`` until the user decides to update.
    """
    version = str(release.get("version") or "")
    asset = release.get("asset") or {}
    url = str(asset.get("url") or "")
    digest = str(asset.get("sha256") or "").lower()
    size = asset.get("size") if isinstance(asset.get("size"), int) else 0
    if not version or not url:
        raise UpdateError("发布信息里没有可下载的安装包。")
    if not digest:
        raise UpdateError("发布方未提供 SHA256，已停止下载。")
    existing = verified_archive(home, version)
    if existing and existing.get("sha256") == digest:
        return {**existing, "reused": True}
    target = staged_archive(home, version, str(asset.get("name") or ""))
    target.parent.mkdir(parents=True, exist_ok=True)
    if size > 0:
        free = shutil.disk_usage(target.parent).free
        if free < size + FREE_SPACE_MARGIN:
            raise UpdateError(f"磁盘空间不足：还需要约 {size / (1024 * 1024):.0f} MB，"
                              f"当前可用 {free / (1024 * 1024):.0f} MB。")
    part = target.with_name(target.name + ".part")
    part.unlink(missing_ok=True)
    hasher = hashlib.sha256()
    received = 0
    try:
        with (stream or _open_download)(url, timeout) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or size or 0)
            with part.open("wb") as handle:
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    if cancelled is not None and cancelled.is_set():
                        raise UpdateError("下载已取消。")
                    handle.write(chunk)
                    hasher.update(chunk)
                    received += len(chunk)
                    if progress is not None:
                        progress(received, total)
                handle.flush()
                os.fsync(handle.fileno())
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    except httpx.HTTPStatusError as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(f"下载失败：HTTP {exc.response.status_code}。") from None
    except (httpx.HTTPError, httpx.InvalidURL, OSError):
        part.unlink(missing_ok=True)
        raise UpdateError("下载失败，请检查网络后重试。") from None
    if size and received != size:
        part.unlink(missing_ok=True)
        raise UpdateError("下载得到的大小与发布信息不一致，已停止。")
    if hasher.hexdigest() != digest:
        part.unlink(missing_ok=True)
        raise UpdateError("校验失败：下载内容与发布的 SHA256 不一致，已删除。")
    os.replace(part, target)
    try:
        layout = validate_archive(target)
    except UpdateError:
        target.unlink(missing_ok=True)
        raise
    record = {"version": version, "name": target.name, "sha256": digest, "size": received, "url": url,
              "verified_at": _now().isoformat(), "entries": layout["entries"]}
    _write_record(target.parent, record)
    return {**record, "path": str(target), "reused": False}
