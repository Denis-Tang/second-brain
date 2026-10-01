"""Small Windows desktop host for the local brain service."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import ctypes
import os
import threading

from .service import BrainService


class DesktopAPI:
    def __init__(self, service: BrainService):
        self._service = service
        self._window = None
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

    def overview(self, period="24h"):
        return self._call(self._service.overview, period)

    def projects(self):
        return self._call(self._service.projects)

    def configure_project(self, name, paths, project_id=""):
        return self._call(self._service.configure_project, name, paths, project_id)

    def delete_project(self, project_id):
        return self._call(self._service.delete_project, project_id)

    def choose_vault(self):
        import webview

        paths = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return {"path": paths[0] if paths else ""}


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
    import pystray
    import webview
    from PIL import Image

    service = BrainService(home)
    api = DesktopAPI(service)
    stop = threading.Event()
    window = webview.create_window(
        "Shared Brain",
        str(Path(__file__).parent / "web" / "index.html"),
        js_api=api,
        width=1120,
        height=780,
        min_size=(860, 620),
        background_color="#f5f7fa",
        text_select=True,
    )
    api._window = window

    def closing():
        if stop.is_set():
            return True
        window.hide()
        return False

    def show_window(*_):
        window.show()
        window.restore()

    def quit_app(icon, _item):
        stop.set()
        icon.stop()
        window.destroy()

    assets = Path(__file__).parent / "web"
    with Image.open(assets / "icon.png") as source:
        icon_image = source.resize((64, 64), Image.Resampling.LANCZOS)
    tray = pystray.Icon(
        "shared-brain",
        icon_image,
        "Shared Brain · 关闭窗口后仍在托盘运行",
        menu=pystray.Menu(
            pystray.MenuItem("显示 Shared Brain", show_window, default=True),
            pystray.MenuItem("退出", quit_app),
        ),
    )
    window.events.closing += closing
    worker = threading.Thread(target=_maintenance_loop, args=(api, stop), daemon=True)
    tray_thread = threading.Thread(target=tray.run, daemon=True)
    worker.start()
    tray_thread.start()
    try:
        webview.start(icon=str(assets / "icon.ico"))
    finally:
        stop.set()
        tray.stop()
        worker.join(timeout=2)
        tray_thread.join(timeout=2)
