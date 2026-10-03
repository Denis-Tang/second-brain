"""Replacement staging and the generated swap script; the real installation is never touched."""

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from shared_brain import desktop, installer, update
from shared_brain.service import BrainService

FAKE_NAMES = ("shared-brain-noop-a", "shared-brain-noop-b")


def make_release_zip(path: Path, extra=None) -> bytes:
    entries = {
        "shared-brain/shared-brain.exe": b"new-exe",
        "shared-brain/desktop/SharedBrain.Desktop.exe": b"new-desktop",
        "shared-brain/runtime/node.exe": b"new-node",
        "shared-brain/integrations/README.md": b"new-readme",
        "shared-brain/_internal/shared_brain/web/app.js": b"new-app",
    }
    entries.update(extra or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return path.read_bytes()


def make_install(root: Path) -> Path:
    install = root / "shared-brain"
    (install / "_internal").mkdir(parents=True)
    (install / "shared-brain.exe").write_bytes(b"old-exe")
    (install / "old-marker.txt").write_text("old", encoding="utf-8")
    return install


def run_script(script: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                          capture_output=True, text=True, timeout=180)


def test_extract_refuses_entries_outside_the_target(tmp_path):
    archive = tmp_path / "evil.zip"
    make_release_zip(archive, {"shared-brain/../../escaped.txt": b"nope"})
    with pytest.raises(update.UpdateError) as excinfo:
        installer.extract_archive(archive, tmp_path / "staging")
    assert "目录外" in str(excinfo.value)
    assert not (tmp_path / "escaped.txt").exists()


def test_extract_and_verify_accept_a_release_layout(tmp_path):
    archive = tmp_path / "release.zip"
    make_release_zip(archive)
    root = tmp_path / "staging"
    assert installer.extract_archive(archive, root) == 5
    assert installer.verify_extracted(root / "shared-brain")["files"] == 5

    broken = tmp_path / "broken"
    installer.extract_archive(archive, broken)
    (broken / "shared-brain" / "shared-brain.exe").unlink()
    with pytest.raises(update.UpdateError):
        installer.verify_extracted(broken / "shared-brain")


def test_prepare_rejects_an_archive_that_does_not_match_the_digest(tmp_path):
    archive = tmp_path / "release.zip"
    make_release_zip(archive)
    install = make_install(tmp_path)
    with pytest.raises(update.UpdateError) as excinfo:
        installer.prepare(tmp_path / "home", install, "0.4.5", "0.4.4", archive, "0" * 64)
    assert "校验失败" in str(excinfo.value)
    assert not (install.parent / ".shared-brain-staging-0.4.5").exists()


def test_prepare_writes_a_readable_script_and_launcher(tmp_path):
    archive = tmp_path / "release.zip"
    payload = make_release_zip(archive)
    install = make_install(tmp_path)
    staged = installer.prepare(tmp_path / "home", install, "0.4.5", "0.4.4", archive,
                               update.file_sha256(archive), launch=False, expect="", names=FAKE_NAMES)
    assert (staged["staged"] / "shared-brain.exe").read_bytes() == b"new-exe"
    assert staged["backup"] == install.with_name("shared-brain.bak-0.4.4")
    ps1 = staged["script"].read_text(encoding="utf-8-sig")
    assert str(install) in ps1 and str(staged["staged"]) in ps1
    assert "已回滚到旧版本" in ps1 and "Move-Item" in ps1 and "$Names" in ps1
    assert "shared-brain-noop-a" in ps1
    launcher = staged["launcher"].read_text(encoding="utf-8")
    assert "ExecutionPolicy Bypass" in launcher and "apply-update.ps1" in launcher


def test_apply_is_blocked_while_other_processes_run(tmp_path, monkeypatch):
    archive = tmp_path / "release.zip"
    make_release_zip(archive)
    install = make_install(tmp_path)
    monkeypatch.setattr(installer, "foreign_processes", lambda: [{"name": "shared-brain.exe", "pid": 4242}])
    result = installer.apply(tmp_path / "home", install, "0.4.5", "0.4.4", archive,
                             update.file_sha256(archive), launch=False, expect="", names=FAKE_NAMES)
    assert result["started"] is False and result["blocked"][0]["pid"] == 4242
    assert "退出" in result["message"]
    assert not (install.parent / ".shared-brain-staging-0.4.5").exists(), "被阻止时不应解压任何东西"


def test_apply_launches_the_script_and_records_the_state(tmp_path, monkeypatch):
    archive = tmp_path / "release.zip"
    make_release_zip(archive)
    install = make_install(tmp_path)
    home = tmp_path / "home"
    launched = []
    monkeypatch.setattr(installer, "foreign_processes", list)
    monkeypatch.setattr(installer, "launch_detached", lambda launcher: launched.append(launcher))
    result = installer.apply(home, install, "0.4.5", "0.4.4", archive, update.file_sha256(archive),
                             launch=False, expect="", names=FAKE_NAMES)
    assert result["started"] and launched and launched[0].name == "apply-update.cmd"
    state = json.loads(installer.state_path(home).read_text(encoding="utf-8"))
    assert state["stage"] == "replacing" and state["version"] == "0.4.5" and state["from_version"] == "0.4.4"
    assert (install / "shared-brain.exe").read_bytes() == b"old-exe", "服务端不得改动安装目录"


def test_pending_notice_reports_unfinished_states(tmp_path):
    home = tmp_path / "home"
    assert installer.pending_notice(home) == ""
    installer.write_state(home, stage="replacing", log="D:/tmp/update.log")
    assert "中断" in installer.pending_notice(home)
    installer.write_state(home, stage="failed", message="新版本自检未通过")
    assert "已回滚" in installer.pending_notice(home)
    installer.write_state(home, stage="done")
    assert installer.pending_notice(home) == ""


def test_foreign_processes_ignores_this_process(monkeypatch):
    monkeypatch.setattr(installer, "list_processes", lambda name=installer.PROCESS_IMAGE: [
        {"name": "shared-brain.exe", "pid": 1}, {"name": "shared-brain.exe", "pid": 2}])
    monkeypatch.setattr(installer.os, "getpid", lambda: 2)
    assert installer.foreign_processes() == [{"name": "shared-brain.exe", "pid": 1}]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows replacement script")
def test_replacement_script_swaps_the_installation(tmp_path):
    archive = tmp_path / "release.zip"
    make_release_zip(archive)
    install = make_install(tmp_path)
    home = tmp_path / "home"
    staged = installer.prepare(home, install, "0.4.5", "0.4.4", archive, update.file_sha256(archive),
                               launch=False, expect="", names=FAKE_NAMES)
    completed = run_script(staged["script"])
    assert completed.returncode == 0, completed.stderr
    assert (install / "shared-brain.exe").read_bytes() == b"new-exe"
    assert (install / "_internal").is_dir()
    assert not (install / "old-marker.txt").exists(), "旧目录应已被换走"
    assert not staged["backup"].exists(), "成功后备份应被清理"
    assert not staged["staging"].exists(), "暂存目录应被清理"
    assert installer.read_state(home)["stage"] == "done"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows replacement script")
def test_replacement_script_rolls_back_after_a_failed_swap(tmp_path):
    install = make_install(tmp_path)
    staged_app = tmp_path / "staging" / installer.PACKAGE_ROOT
    (staged_app / "_internal").mkdir(parents=True)
    (staged_app / "shared-brain.exe").write_bytes(b"new-exe")
    backup = install.with_name("shared-brain.bak-0.4.4")
    home = tmp_path / "home"
    home.mkdir()
    blocked = home / "blocked"          # 让写状态这一步失败，触发替换后的回滚分支
    blocked.write_text("file", encoding="utf-8")
    scripts = installer.write_scripts(tmp_path / "staging", install=install, staged=staged_app, backup=backup,
                                      exe=staged_app / "shared-brain.exe", state=blocked / "state.json",
                                      log=tmp_path / "update.log", version="0.4.5", expect="", launch=False,
                                      names=FAKE_NAMES)
    run_script(scripts["script"])
    assert (install / "shared-brain.exe").read_bytes() == b"old-exe", "失败后应回滚到旧版本"
    assert (install / "old-marker.txt").exists()
    assert not backup.exists()
    assert "已回滚到旧版本" in (tmp_path / "update.log").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows replacement script")
def test_replacement_script_leaves_everything_alone_when_staging_is_gone(tmp_path):
    install = make_install(tmp_path)
    home = tmp_path / "home"
    archive = tmp_path / "release.zip"
    make_release_zip(archive)
    staged = installer.prepare(home, install, "0.4.5", "0.4.4", archive, update.file_sha256(archive),
                               launch=False, expect="", names=FAKE_NAMES)
    shutil.rmtree(staged["staged"])  # 只删暂存的应用目录，脚本本身留在暂存根目录里
    run_script(staged["script"])
    assert (install / "shared-brain.exe").read_bytes() == b"old-exe"
    assert (install / "old-marker.txt").exists()
    assert installer.read_state(home)["stage"] == "failed"
    assert "暂存目录不存在" in installer.read_state(home)["message"]


def test_service_refuses_to_replace_when_running_from_source(tmp_path):
    service = BrainService(tmp_path / "app")
    result = service.apply_update()
    assert result["started"] is False and "源码运行" in result["message"]


def test_desktop_asks_the_window_to_quit_after_starting_a_replacement(tmp_path, monkeypatch):
    service = BrainService(tmp_path / "app")
    api = desktop.DesktopAPI(service)
    monkeypatch.setattr(service, "apply_update", lambda: {"started": True, "message": "正在退出并替换"})
    assert api.apply_update()["started"] and api._quit_requested

    api2 = desktop.DesktopAPI(service)
    monkeypatch.setattr(service, "apply_update", lambda: {"started": False, "message": "已是最新版本"})
    assert api2.apply_update()["started"] is False and not api2._quit_requested


def test_desktop_reports_an_unfinished_replacement_once(tmp_path):
    home = tmp_path / "app"
    api = desktop.DesktopAPI(BrainService(home))
    installer.write_state(home, stage="failed", message="新版本自检未通过")
    first = api.status()
    assert "已回滚" in first["background_error"]
    assert "background_error" not in api.status()
