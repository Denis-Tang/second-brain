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

from .service import BrainService


class DesktopAPI:
    def __init__(self, service: BrainService):
        self._service = service
        self._background_error = ""

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

    def overview(self, period="24h"):
        return self._call(self._service.overview, period)

    def projects(self):
        return self._call(self._service.projects)

    def configure_project(self, name, paths, project_id=""):
        return self._call(self._service.configure_project, name, paths, project_id)

    def delete_project(self, project_id):
        return self._call(self._service.delete_project, project_id)

def _maintenance_loop(api: DesktopAPI, stop: threading.Event):
    ran_on = None
    while not stop.wait(30):
        now = datetime.now()
        try:
            state = api._service.status()
            settings = state["settings"]
            if not (state["ready"] and settings["maintenance_enabled"] and state["pending"]):
                continue
            hour, minute = map(int, settings["maintenance_time"].split(":"))
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < scheduled or ran_on == now.date():
                continue
            ran_on = now.date()
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
        finally:
            stop.set()
            process.stdin.close()
            worker.join(timeout=2)
