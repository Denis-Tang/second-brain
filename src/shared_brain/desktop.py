"""Small Windows desktop host for the local brain service."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import ctypes
import os
import json
import subprocess
import sys
import threading

from . import installer, update
from .service import BrainService


class DesktopAPI:
    def __init__(self, service: BrainService):
        self._service = service
        self._background_error = ""
        self._update_lock = threading.Lock()
        self._update_cancel = threading.Event()
        self._update_thread = None
        self._quit_requested = False
        self._update_notice_shown = False
        self._update_state = {"stage": "idle", "version": "", "received": 0, "total": 0,
                              "percent": 0, "message": "", "path": "", "started_at": ""}

    def _call(self, method, *args):
        try:
            return method(*args)
        except Exception as exc:
            return {"error": str(exc)}

    def status(self):
        result = self._call(self._service.status)
        if self._background_error:
            result["background_error"] = self._background_error
            self._background_error = ""
        elif not self._update_notice_shown:
            notice = installer.pending_notice(self._service.settings.home)
            if notice:
                self._update_notice_shown = True
                result["background_error"] = notice
        return result

    def initialize(self, vault_path):
        return self._call(self._service.initialize, vault_path)

    def configure(self, values, api_key=None):
        return self._call(self._service.configure, values, api_key)

    def save_global_prompt(self, text):
        return self._call(self._service.save_global_prompt, text)

    def test_connection(self):
        return self._call(self._service.test_connection)

    def agent_prompt(self):
        return self._call(self._service.agent_prompt)

    def unbind_prompt(self):
        return self._call(self._service.unbind_prompt)

    def check_update(self):
        return self._call(self._service.check_update)

    def open_release_page(self):
        return self._call(self._service.open_release_page)

    def download_update(self):
        return self._call(self._start_download)

    def update_download_state(self):
        return self._call(self._download_snapshot)

    def cancel_download(self):
        return self._call(self._cancel_download)

    def open_download_folder(self, path=""):
        return self._call(self._open_download_folder, path)

    def apply_update(self):
        result = self._call(self._service.apply_update)
        if isinstance(result, dict) and result.get("started"):
            # The detached script waits for this process to disappear, so leave once answered.
            self._quit_requested = True
        return result

    def _snapshot_locked(self):
        state = dict(self._update_state)
        total = state.get("total") or 0
        received = state.get("received") or 0
        if state["stage"] == "done":
            state["percent"] = 100
        elif total > 0:
            state["percent"] = min(99, int(received * 100 / total))
        else:
            state["percent"] = 0
        return state

    def _download_snapshot(self):
        with self._update_lock:
            return self._snapshot_locked()

    def _start_download(self):
        # The release lookup runs outside the lock so progress polling is never blocked by it.
        release = self._service.check_update()
        with self._update_lock:
            if self._update_state["stage"] == "downloading":
                return {**self._snapshot_locked(), "started": False, "message": "已经有下载在进行。"}
        if not release.get("newer"):
            return {**self._download_snapshot(), "started": False,
                    "message": release.get("message") or "没有可下载的新版本。"}
        asset = release.get("asset") or {}
        if not asset.get("url") or not asset.get("sha256"):
            return {**self._download_snapshot(), "started": False,
                    "message": "发布信息里没有可下载且可校验的安装包。"}
        size = asset.get("size") if isinstance(asset.get("size"), int) else 0
        with self._update_lock:
            if self._update_state["stage"] == "downloading":
                return {**self._snapshot_locked(), "started": False, "message": "已经有下载在进行。"}
            self._update_cancel = threading.Event()
            self._update_state = {"stage": "downloading", "version": str(release.get("latest") or ""),
                                  "received": 0, "total": size, "percent": 0, "message": "正在下载…",
                                  "path": "", "started_at": datetime.now().isoformat()}
            thread = threading.Thread(target=self._download_worker, args=(release,), daemon=True)
            self._update_thread = thread
            thread.start()
            return {**self._snapshot_locked(), "started": True}

    def _download_worker(self, release):
        def progress(received, total):
            with self._update_lock:
                self._update_state["received"] = received
                if total:
                    self._update_state["total"] = total
                self._update_state["message"] = f"正在下载 {received / 1048576:.1f} MB"

        try:
            record = update.download_release(self._service.settings.home, release,
                                             progress=progress, cancelled=self._update_cancel)
        except update.UpdateError as exc:
            with self._update_lock:
                self._update_state.update(stage="failed", message=str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a background failure must not close the window
            with self._update_lock:
                self._update_state.update(stage="failed", message=f"下载失败：{exc}")
            return
        with self._update_lock:
            size = record.get("size") or 0
            self._update_state.update(stage="done", path=str(record.get("path") or ""), received=size, total=size,
                                      reused=bool(record.get("reused")), verified_at=str(record.get("verified_at") or ""),
                                      message=("已存在校验通过的安装包，未重复下载。" if record.get("reused")
                                               else "已下载并校验，可交给 Agent 替换（本程序没有替换任何文件）。"))

    def _cancel_download(self):
        self._update_cancel.set()
        with self._update_lock:
            if self._update_state["stage"] == "downloading":
                self._update_state["message"] = "正在取消…"
            return self._snapshot_locked()

    def _open_download_folder(self, path=""):
        candidate = Path(path) if path else Path(str(self._update_state.get("path") or ""))
        folder = candidate.parent if candidate.name else update.updates_directory(self._service.settings.home)
        folder.mkdir(parents=True, exist_ok=True)
        command = ["explorer", "/select,", str(candidate)] if candidate.is_file() else ["explorer", str(folder)]
        subprocess.Popen(command)
        return {"path": str(folder)}

    def overview(self, period="24h"):
        return self._call(self._service.overview, period)

    def projects(self):
        return self._call(self._service.projects)

    def configure_project(self, name, paths, project_id="", parent_project_id=""):
        return self._call(self._service.configure_project, name, paths, project_id, parent_project_id)

    def project_changes(self, project_id):
        return self._call(self._service.project_changes, project_id)

    def create_commit(self, project_id, item_ids, message=""):
        return self._call(self._service.create_commit, project_id, item_ids, message)

    def project_commits(self, project_id):
        return self._call(self._service.project_commits, project_id)

    def project_commit(self, project_id, commit_id):
        return self._call(self._service.project_commit, project_id, commit_id)

    def merge_commit(self, project_id, commit_id, resolutions=None):
        return self._call(self._service.merge_commit, project_id, commit_id, resolutions)

    def documents(self, project="", query=""):
        return self._call(self._service.documents, project, query)

    def document(self, path):
        return self._call(self._service.document, path)

    def open_document(self, path):
        return self._call(self._service.open_document, path)

    def delete_project(self, project_id):
        return self._call(self._service.delete_project, project_id)

def _maintenance_loop(api: DesktopAPI, stop: threading.Event):
    ran_on = None
    while not stop.wait(30):
        now = datetime.now()
        try:
            state = api._service.status()
            settings = state["settings"]
            if not (state["ready"] and settings["maintenance_enabled"]):
                continue
            hour, minute = map(int, settings["maintenance_time"].split(":"))
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < scheduled or ran_on == now.date():
                continue
            ran_on = now.date()
            # maintain() rescans the vault itself, so a stale pending count cannot postpone the run.
            api._service.maintain()
        except Exception as exc:
            api._background_error = f"自动维护失败：{exc}"


def run(home: Path | None = None):
    if os.name != "nt":
        return _run_desktop(home)

    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Local\\SharedBrain.Desktop")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    already_running = ctypes.get_last_error() == 183
    try:
        user = ctypes.WinDLL("user32")
        user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user.FindWindowW.restype = wintypes.HWND
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        window = user.FindWindowW(None, "Shared Brain")
        if already_running or window:
            if window:
                user.ShowWindow(window, 9)  # Restore the existing tray window.
                user.SetForegroundWindow(window)
            return
        return _run_desktop(home)
    finally:
        kernel.CloseHandle(handle)


def _run_desktop(home: Path | None = None):
    service = BrainService(home)
    api = DesktopAPI(service)
    stop = threading.Event()
    assets = Path(__file__).parent / "web"
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2] / "build"
    host = root / "desktop" / "SharedBrain.Desktop.exe"
    worker = threading.Thread(target=_maintenance_loop, args=(api, stop), daemon=True)
    with subprocess.Popen([str(host), str(assets), str(service.settings.home / "webview")],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8") as process:
        worker.start()
        try:
            for line in process.stdout:
                request = json.loads(line)
                result = getattr(api, request["method"])(*request["args"])
                process.stdin.write(json.dumps({"id": request["id"], "result": result}, ensure_ascii=False) + "\n")
                process.stdin.flush()
                if api._quit_requested:
                    # Answer first, then let the replacement script do its work.
                    break
        finally:
            stop.set()
            process.stdin.close()
            worker.join(timeout=2)
