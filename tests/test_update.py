"""Version checking stays read-only: no downloads, no installs, no network in tests."""

import hashlib
import json
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from shared_brain import __version__, desktop, update
from shared_brain.onboarding import build_update_prompt
from shared_brain.service import BrainService

major, minor, patch = __version__.split(".")
NEWER_VERSION = f"{major}.{minor}.{int(patch) + 1}"

PACKAGE_FILES = [
    "shared-brain/shared-brain.exe",
    "shared-brain/desktop/SharedBrain.Desktop.exe",
    "shared-brain/runtime/node.exe",
    "shared-brain/integrations/README.md",
    "shared-brain/_internal/shared_brain/web/app.js",
]


def make_archive(path: Path, names=None) -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names or PACKAGE_FILES:
            archive.writestr(name, b"payload")
    return path.read_bytes()


class FakeStream:
    """Stands in for httpx's streaming response context manager."""

    def __init__(self, payload: bytes, status_code: int = 200, chunk: int = 4096):
        self.payload = payload
        self.status_code = status_code
        self.chunk = chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @property
    def headers(self):
        return {"content-length": str(len(self.payload))}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.invalid/pkg.zip")
            raise httpx.HTTPStatusError("bad", request=request,
                                        response=httpx.Response(self.status_code, request=request))

    def iter_bytes(self, size=0):
        for index in range(0, len(self.payload), self.chunk):
            yield self.payload[index:index + self.chunk]


def release_for(payload: bytes, name="shared-brain-0.4.5-windows-x64.zip", **overrides):
    asset = {"name": name, "url": f"https://example.invalid/{name}", "size": len(payload),
             "sha256": hashlib.sha256(payload).hexdigest()}
    asset.update(overrides.pop("asset", {}))
    return {"tag": "v0.4.5", "version": "0.4.5", "published_at": "2026-10-05T00:00:00Z", "notes": "",
            "notes_truncated": False, "page": update.RELEASE_PAGE, "asset": asset, **overrides}


def release(version="0.4.5", notes="本版改进检索。"):
    return {
        "tag": "v" + version,
        "version": version,
        "published_at": "2026-10-05T00:00:00Z",
        "notes": notes,
        "notes_truncated": False,
        "page": update.RELEASE_PAGE,
        "asset": {"name": f"shared-brain-{version}-windows-x64.zip",
                  "url": f"https://example.invalid/shared-brain-{version}-windows-x64.zip",
                  "size": 152183570, "sha256": "a" * 64},
    }


def failing(message="GitHub 接口查询次数已用完，请在 09:30 之后再试。"):
    def fetcher(timeout):
        raise update.UpdateError(message)
    return fetcher


def test_version_comparison_is_numeric_not_textual():
    assert update.is_newer("v0.4.10", "0.4.9")
    assert update.is_newer("0.5.0", "0.4.99")
    assert not update.is_newer("v0.4.4", "0.4.4")
    assert not update.is_newer("v0.4.3", "0.4.4")
    assert update.is_newer("v0.4.5-rc.1", "0.4.4")
    assert not update.is_newer("v0.4.4-rc.1", "0.4.4")
    assert not update.is_newer("", "0.4.4")
    assert not update.is_newer("nightly", "0.4.4")


def test_newer_release_is_reported_and_cached(tmp_path):
    calls = []

    def fetcher(timeout):
        calls.append(timeout)
        return release(NEWER_VERSION)

    result = update.check_latest(tmp_path, force=True, fetcher=fetcher)
    assert result["newer"] and result["available"] and not result["stale"]
    assert result["latest"] == NEWER_VERSION and result["current"] == __version__
    assert result["message"].startswith("发现新版本")
    assert result["asset"]["sha256"] == "a" * 64
    assert calls == [8.0]

    cached = json.loads((tmp_path / update.CACHE_NAME).read_text(encoding="utf-8"))
    assert cached["release"]["version"] == NEWER_VERSION
    # A second click reuses the cached answer instead of spending another API call.
    again = update.check_latest(tmp_path, force=True, fetcher=failing("不该被调用"))
    assert again["newer"] and again["cached"] and not calls[1:]


def test_expired_cache_without_force_reuses_the_stored_answer(tmp_path):
    moment = datetime.now(timezone.utc)
    update._write_cache(tmp_path, release(NEWER_VERSION), moment - timedelta(hours=2))
    result = update.check_latest(tmp_path, fetcher=failing("不该被调用"))
    assert result["newer"] and result["cached"] and result["available"]


def test_unavailable_github_falls_back_to_a_stale_cache(tmp_path):
    moment = datetime.now(timezone.utc)
    update._write_cache(tmp_path, release(NEWER_VERSION), moment - timedelta(days=2))
    result = update.check_latest(tmp_path, force=True, fetcher=failing())
    assert result["stale"] and result["available"] and result["newer"]
    assert "查询次数已用完" in result["message"]


def test_unavailable_github_without_cache_reports_a_short_message(tmp_path):
    result = update.check_latest(tmp_path, force=True, fetcher=failing("无法连接 GitHub，请检查网络后重试。"))
    assert not result["available"] and not result["newer"] and not result["stale"]
    assert result["message"] == "无法连接 GitHub，请检查网络后重试。"
    assert not (tmp_path / update.CACHE_NAME).exists()


def test_corrupt_cache_is_ignored(tmp_path):
    (tmp_path / update.CACHE_NAME).write_text("{ not json", encoding="utf-8")
    result = update.check_latest(tmp_path, force=True, fetcher=lambda timeout: release(NEWER_VERSION))
    assert result["available"] and result["newer"]


def test_fetch_latest_handles_http_statuses(monkeypatch):
    def respond(status, **kwargs):
        monkeypatch.setattr(update.httpx, "get", lambda *_, **__: httpx.Response(
            status_code=status, headers=kwargs.pop("headers", {}), request=httpx.Request("GET", update.LATEST_RELEASE_API), **kwargs))

    respond(403, headers={"x-ratelimit-reset": "1790000000"})
    with pytest.raises(update.UpdateError) as limited:
        update.fetch_latest()
    assert "查询次数已用完" in str(limited.value)

    respond(404)
    with pytest.raises(update.UpdateError):
        update.fetch_latest()

    respond(500)
    with pytest.raises(update.UpdateError) as broken:
        update.fetch_latest()
    assert "HTTP 500" in str(broken.value)

    respond(200, text="not json")
    with pytest.raises(update.UpdateError):
        update.fetch_latest()

    respond(200, json={"tag_name": "", "assets": []})
    with pytest.raises(update.UpdateError):
        update.fetch_latest()


def test_malformed_proxy_settings_degrade_instead_of_escaping(tmp_path, monkeypatch):
    # httpx.InvalidURL is raised for a malformed proxy setting and is not an httpx.HTTPError.
    def broken(*_, **__):
        raise httpx.InvalidURL("Invalid port: ':1]'")

    monkeypatch.setattr(update.httpx, "get", broken)
    with pytest.raises(update.UpdateError):
        update.fetch_latest()
    result = update.check_latest(tmp_path, force=True, timeout=1)
    assert not result["available"] and result["message"] == "无法连接 GitHub，请检查网络后重试。"


def test_fetch_latest_selects_the_windows_asset(monkeypatch):
    payload = {
        "tag_name": "v0.4.5",
        "published_at": "2026-10-05T00:00:00Z",
        "body": "x" * 5000,
        "assets": [
            {"name": "source.tar.gz", "browser_download_url": "https://example.invalid/s.tar.gz"},
            {"name": "shared-brain-0.4.5-windows-x64.zip", "browser_download_url": "https://example.invalid/win.zip",
             "size": 152183570, "digest": "sha256:" + "b" * 64},
        ],
    }
    monkeypatch.setattr(update.httpx, "get", lambda *_, **__: httpx.Response(
        status_code=200, json=payload, request=httpx.Request("GET", update.LATEST_RELEASE_API)))
    result = update.fetch_latest()
    assert result["version"] == "0.4.5"
    assert result["asset"]["url"] == "https://example.invalid/win.zip"
    assert result["asset"]["sha256"] == "b" * 64
    assert result["notes_truncated"] and len(result["notes"]) == update.NOTES_LIMIT


def test_update_prompt_keeps_the_install_path_and_protects_user_data(tmp_path):
    install = Path("D:/apps/shared-brain")
    config = {"mcpServers": {"shared_brain": {"command": str(install / "shared-brain.exe"),
                                              "args": ["--home", str(tmp_path), "mcp"]}}}
    text = build_update_prompt(install, tmp_path, config, "0.4.4", release())
    assert str(install) in text
    assert "0.4.4 更新到 0.4.5" in text
    assert "https://example.invalid/shared-brain-0.4.5-windows-x64.zip" in text
    assert "a" * 64 in text
    assert "路径不变就不需要改任何配置" in text
    assert "145.1 MB" in text
    assert "shared-brain.bak-0.4.4" in text
    assert str(tmp_path) in text


def test_source_update_prompt_tells_the_agent_to_pull_and_rebuild(tmp_path):
    config = {"mcpServers": {"shared_brain": {"command": "python", "args": ["-m", "shared_brain", "--home", str(tmp_path), "mcp"]}}}
    text = build_update_prompt(None, tmp_path, config, "0.4.4", release())
    assert "git pull" in text and "scripts/build.ps1" in text
    assert ".bak" not in text


def test_status_exposes_the_version_and_check_update_builds_the_prompt(tmp_path, monkeypatch):
    service = BrainService(tmp_path / "app")
    assert service.status()["version"] == __version__
    assert service.installation_directory() is None  # running from source in tests

    monkeypatch.setattr(update, "check_latest", lambda home, force=False: {
        "current": __version__, "latest": "0.4.5", "newer": True, "available": True, "stale": False,
        "published_at": "2026-10-05T00:00:00Z", "notes": "", "notes_truncated": False,
        "asset": {"size": 1, "sha256": "c" * 64, "url": "https://example.invalid/win.zip"},
        "page": update.RELEASE_PAGE, "cached": False, "checked_at": "2026-10-05T00:00:00+00:00",
        "message": "发现新版本 v0.4.5。"})
    result = service.check_update()
    assert result["newer"] and result["install_directory"] == ""
    assert "git pull" in result["prompt"]
    assert f"更新到 0.4.5" in result["prompt"]

    monkeypatch.setattr(update, "check_latest", lambda home, force=False: {
        "current": __version__, "latest": "0.4.4", "newer": False, "available": True, "stale": False,
        "published_at": "", "notes": "", "notes_truncated": False, "asset": {}, "page": update.RELEASE_PAGE,
        "cached": False, "checked_at": "", "message": "已是最新版本。"})
    assert service.check_update()["prompt"] == ""


def test_desktop_bridge_checks_updates_and_opens_the_release_page(tmp_path, monkeypatch):
    api = desktop.DesktopAPI(BrainService(tmp_path / "app"))
    monkeypatch.setattr(update, "check_latest", lambda home, force=False: {
        "current": __version__, "latest": "", "newer": False, "available": False, "stale": False,
        "published_at": "", "notes": "", "notes_truncated": False, "asset": {}, "page": update.RELEASE_PAGE,
        "cached": False, "checked_at": "", "message": "无法连接 GitHub，请检查网络后重试。"})
    assert api.check_update()["message"].startswith("无法连接 GitHub")

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    assert api.open_release_page() == {"opened": True, "url": update.RELEASE_PAGE}
    assert opened == [update.RELEASE_PAGE]

    monkeypatch.setattr("webbrowser.open", lambda url: (_ for _ in ()).throw(OSError("no browser")))
    assert api.open_release_page()["opened"] is False


def test_validate_archive_accepts_the_release_layout_and_rejects_the_rest(tmp_path):
    good = tmp_path / "good.zip"
    make_archive(good)
    assert update.validate_archive(good) == {"entries": len(PACKAGE_FILES)}

    flat = tmp_path / "flat.zip"
    make_archive(flat, ["shared-brain.exe", "README.md"])
    with pytest.raises(update.UpdateError):
        update.validate_archive(flat)

    incomplete = tmp_path / "incomplete.zip"
    make_archive(incomplete, ["shared-brain/shared-brain.exe", "shared-brain/_internal/x.js"])
    with pytest.raises(update.UpdateError) as missing:
        update.validate_archive(incomplete)
    assert "缺少必要文件" in str(missing.value)

    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip at all")
    with pytest.raises(update.UpdateError):
        update.validate_archive(broken)


def test_download_release_streams_verifies_and_records(tmp_path):
    payload = make_archive(tmp_path / "source.zip")
    release = release_for(payload)
    seen = []

    def stream(url, timeout):
        seen.append(url)
        return FakeStream(payload)

    result = update.download_release(tmp_path, release, stream=stream)
    assert not result["reused"] and result["sha256"] == release["asset"]["sha256"]
    path = Path(result["path"])
    assert path.is_file() and path.read_bytes() == payload
    assert path.parent == update.updates_directory(tmp_path) / "0.4.5"
    assert seen == [release["asset"]["url"]]

    record = json.loads((path.parent / update.RECORD_NAME).read_text(encoding="utf-8"))
    assert record["version"] == "0.4.5" and record["entries"] == len(PACKAGE_FILES)
    assert update.verified_archive(tmp_path, "0.4.5")["path"] == str(path)

    def forbidden(url, timeout):
        raise AssertionError("已校验的包不应重新下载")

    again = update.download_release(tmp_path, release, stream=forbidden)
    assert again["reused"] and again["path"] == str(path)


def test_download_release_reports_progress(tmp_path):
    payload = make_archive(tmp_path / "source.zip")
    samples = []
    update.download_release(tmp_path, release_for(payload),
                            progress=lambda received, total: samples.append((received, total)),
                            stream=lambda url, timeout: FakeStream(payload, chunk=1000))
    assert samples[-1] == (len(payload), len(payload))
    assert [received for received, _ in samples] == sorted(received for received, _ in samples)


def test_download_release_rejects_a_digest_mismatch(tmp_path):
    payload = make_archive(tmp_path / "source.zip")
    release = release_for(payload, asset={"sha256": "b" * 64})
    with pytest.raises(update.UpdateError) as excinfo:
        update.download_release(tmp_path, release, stream=lambda url, timeout: FakeStream(payload))
    assert "校验失败" in str(excinfo.value)
    folder = update.updates_directory(tmp_path) / "0.4.5"
    assert not list(folder.glob("*.zip")) and not list(folder.glob("*.part"))


def test_download_release_rejects_a_size_mismatch(tmp_path):
    payload = make_archive(tmp_path / "source.zip")
    release = release_for(payload, asset={"size": len(payload) + 10})
    with pytest.raises(update.UpdateError) as excinfo:
        update.download_release(tmp_path, release, stream=lambda url, timeout: FakeStream(payload))
    assert "大小" in str(excinfo.value)
    assert not list((update.updates_directory(tmp_path) / "0.4.5").glob("*.part"))


def test_download_release_requires_a_published_digest(tmp_path):
    payload = make_archive(tmp_path / "source.zip")
    release = release_for(payload, asset={"sha256": ""})
    with pytest.raises(update.UpdateError) as excinfo:
        update.download_release(tmp_path, release, stream=lambda url, timeout: FakeStream(payload))
    assert "SHA256" in str(excinfo.value)


def test_download_release_cancel_leaves_no_partial_file(tmp_path):
    payload = make_archive(tmp_path / "source.zip")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(update.UpdateError) as excinfo:
        update.download_release(tmp_path, release_for(payload), cancelled=cancel,
                                stream=lambda url, timeout: FakeStream(payload))
    assert "取消" in str(excinfo.value)
    folder = update.updates_directory(tmp_path) / "0.4.5"
    assert not list(folder.glob("*.part")) and not list(folder.glob("*.zip"))


def test_download_release_removes_a_package_with_the_wrong_layout(tmp_path):
    payload = make_archive(tmp_path / "source.zip", ["shared-brain/README.md"])
    with pytest.raises(update.UpdateError) as excinfo:
        update.download_release(tmp_path, release_for(payload), stream=lambda url, timeout: FakeStream(payload))
    assert "缺少必要文件" in str(excinfo.value)
    assert not list((update.updates_directory(tmp_path) / "0.4.5").glob("*.zip"))
    assert not update.verified_archive(tmp_path, "0.4.5")


def test_download_release_reports_network_failures(tmp_path, monkeypatch):
    payload = make_archive(tmp_path / "source.zip")
    release = release_for(payload)

    def broken(url, timeout):
        raise httpx.InvalidURL("Invalid port: ':1]'")

    with pytest.raises(update.UpdateError) as excinfo:
        update.download_release(tmp_path, release, stream=broken)
    assert "下载失败" in str(excinfo.value)

    with pytest.raises(update.UpdateError) as excinfo:
        update.download_release(tmp_path, release, stream=lambda url, timeout: FakeStream(payload, status_code=404))
    assert "HTTP 404" in str(excinfo.value)
    assert not list((update.updates_directory(tmp_path) / "0.4.5").glob("*.part"))


def test_download_release_refuses_when_disk_is_full(tmp_path, monkeypatch):
    payload = make_archive(tmp_path / "source.zip")
    monkeypatch.setattr(update.shutil, "disk_usage", lambda path: SimpleNamespace(free=1024))
    with pytest.raises(update.UpdateError) as excinfo:
        update.download_release(tmp_path, release_for(payload), stream=lambda url, timeout: FakeStream(payload))
    assert "磁盘空间不足" in str(excinfo.value)


def test_desktop_download_runs_in_the_background(tmp_path, monkeypatch):
    service = BrainService(tmp_path / "app")
    api = desktop.DesktopAPI(service)
    payload = make_archive(tmp_path / "source.zip")
    release = release_for(payload)
    archive = tmp_path / "pkg.zip"
    archive.write_bytes(payload)

    def check_update(force=True):
        return {"current": __version__, "latest": "0.4.5", "newer": True, "available": True, "stale": False,
                "published_at": "", "notes": "", "notes_truncated": False, "asset": release["asset"],
                "page": update.RELEASE_PAGE, "cached": False, "checked_at": "", "message": "发现新版本 v0.4.5。",
                "install_directory": "", "prompt": "", "local_archive": "", "local_verified_at": ""}

    monkeypatch.setattr(service, "check_update", check_update)

    def fake_download(home, release, progress=None, cancelled=None):
        progress(1024, 2048)
        return {"version": "0.4.5", "name": archive.name, "sha256": release["asset"]["sha256"], "size": 2048,
                "url": release["asset"]["url"], "verified_at": "2026-10-05T00:00:00+00:00", "entries": 6,
                "path": str(archive), "reused": False}

    monkeypatch.setattr(update, "download_release", fake_download)
    started = api.download_update()
    assert started["started"] and started["stage"] == "downloading"
    api._update_thread.join(timeout=5)
    state = api.update_download_state()
    assert state["stage"] == "done" and state["percent"] == 100 and state["path"] == str(archive)
    assert "没有替换任何文件" in state["message"]

    monkeypatch.setattr(service, "check_update", lambda force=True: {"newer": False, "message": "已是最新版本 v0.4.4。"})
    stopped = api.download_update()
    assert stopped["started"] is False and stopped["message"] == "已是最新版本 v0.4.4。"


def test_desktop_download_failure_is_reported(tmp_path, monkeypatch):
    service = BrainService(tmp_path / "app")
    api = desktop.DesktopAPI(service)
    monkeypatch.setattr(service, "check_update", lambda force=True: {
        "newer": True, "latest": "0.4.5", "message": "发现新版本 v0.4.5。",
        "asset": {"url": "https://example.invalid/pkg.zip", "sha256": "c" * 64, "size": 10}})

    def failing(home, release, progress=None, cancelled=None):
        raise update.UpdateError("下载失败，请检查网络后重试。")

    monkeypatch.setattr(update, "download_release", failing)
    assert api.download_update()["started"]
    api._update_thread.join(timeout=5)
    state = api.update_download_state()
    assert state["stage"] == "failed" and state["message"] == "下载失败，请检查网络后重试。"


def test_desktop_cancel_and_open_folder(tmp_path, monkeypatch):
    api = desktop.DesktopAPI(BrainService(tmp_path / "app"))
    api._update_state.update(stage="downloading", message="正在下载…")
    assert "取消" in api.cancel_download()["message"]
    assert api._update_cancel.is_set()

    commands = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda command, **kwargs: commands.append(command))
    archive = tmp_path / "updates" / "0.4.5" / "pkg.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"x")
    assert api.open_download_folder(str(archive))["path"] == str(archive.parent)
    assert commands == [["explorer", "/select,", str(archive)]]

    commands.clear()
    api.open_download_folder("")
    assert commands == [["explorer", str(update.updates_directory(api._service.settings.home))]]
