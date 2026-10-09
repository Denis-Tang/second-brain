"""Exercise the unmodified packaged WPF host with isolated WebView2 profiles."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import pytest

from shared_brain.desktop import DesktopAPI
from shared_brain.service import BrainService


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native desktop")


@pytest.fixture
def native(tmp_path):
    executable = os.environ.get("SHARED_BRAIN_TEST_EXE")
    if not executable:
        pytest.skip("Set SHARED_BRAIN_TEST_EXE to a freshly built package")
    package = Path(executable).resolve().parent
    host = package / "desktop/SharedBrain.Desktop.exe"
    assert host.is_file(), host
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("icon.ico", "bridge.js"):
        shutil.copyfile(package / "_internal/shared_brain/web" / name, assets / name)
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.EnumChildWindows.argtypes = [wintypes.HWND, callback, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    for name in ("IsWindowVisible", "IsIconic", "IsZoomed"):
        getattr(user, name).argtypes = [wintypes.HWND]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_int]

    class Native:
        def text(self, handle):
            value = ctypes.create_unicode_buffer(8192)
            user.GetWindowTextW(handle, value, len(value))
            return value.value

        def window(self, pid, title):
            found = []

            @callback
            def visit(handle, _):
                owner = wintypes.DWORD()
                user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
                if owner.value == pid and self.text(handle) == title:
                    found.append(handle)
                return True

            user.EnumWindows(visit, 0)
            return found[0] if found else None

        def children_text(self, handle):
            values = []

            @callback
            def visit(child, _):
                values.append(self.text(child))
                return True

            user.EnumChildWindows(handle, visit, 0)
            return "\n".join(values)

        def attribute(self, handle, attribute):
            value = ctypes.c_int()
            assert dwm.DwmGetWindowAttribute(handle, attribute, ctypes.byref(value), 4) == 0
            return value.value

        @contextmanager
        def launch(self, profile, **environment):
            env = dict(os.environ)
            for key in ("WEBVIEW2_USER_DATA_FOLDER", "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"):
                env.pop(key, None)
            env.update(environment)
            process = subprocess.Popen([str(host), str(assets), str(profile)], env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding="utf-8")
            try:
                yield process
            finally:
                # A failed SDK test can leave a blocking Edge child dialog. Kill the tree first.
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=10)
                    process.wait(timeout=10)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()

    driver = Native()
    driver.assets, driver.user = assets, user
    return driver


def wait_for(check):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    pytest.fail("Native desktop did not reach the expected state within 15 seconds")


def test_native_first_frame_and_restore_have_rendered_pixels(tmp_path, native):
    from PIL import ImageGrab

    (native.assets / "index.html").write_text('''<!doctype html>
<style>html,body{margin:0;width:100%;height:100%;background:#e02060}
div{position:absolute;left:50%;width:50%;height:100%;background:#20d080}</style><div></div>''',
                                            encoding="utf-8")
    profile = tmp_path / "render-profile"
    for _ in range(2):  # Fresh profile, then the same profile after a normal exit.
        with native.launch(profile) as process:
            handle = wait_for(lambda: native.window(process.pid, "Shared Brain"))
            wait_for(lambda: native.attribute(handle, 14) == 0)

            def painted():
                frame = ImageGrab.grab(window=handle)
                left = frame.getpixel((frame.width // 4, frame.height // 2))[:3]
                right = frame.getpixel((frame.width * 3 // 4, frame.height // 2))[:3]
                return all(abs(a - b) < 12 for actual, expected in
                           ((left, (224, 32, 96)), (right, (32, 208, 128)))
                           for a, b in zip(actual, expected))

            wait_for(painted)
            for _ in range(3):
                native.user.ShowWindow(handle, 6)
                wait_for(lambda: native.user.IsIconic(handle))
                native.user.ShowWindow(handle, 9)
                wait_for(lambda: not native.user.IsIconic(handle))
                wait_for(painted)
            process.stdin.close()
            assert process.wait(timeout=10) == 0


@pytest.mark.parametrize("failure", ["profile-file", "browser-data-file", "missing-runtime"])
def test_native_startup_error_returns_without_edge_dialog(tmp_path, native, failure):
    profile = tmp_path / "界面数据"
    sentinel = profile if failure == "profile-file" else profile / "EBWebView"
    environment = {}
    if failure == "missing-runtime":
        environment["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] = str(tmp_path / "missing-runtime")
    else:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("preserve existing data", encoding="utf-8")
    with native.launch(profile, **environment) as process:
        dialog = wait_for(lambda: native.window(process.pid, "Shared Brain 启动失败"))
        expected = "无法初始化 WebView2" if failure == "missing-runtime" else "无法创建或访问界面数据目录"
        wait_for(lambda: expected in native.children_text(dialog))
        message = native.children_text(dialog)
        assert expected in message and str(profile) in message and "0x" in message
        assert native.user.PostMessageW(dialog, 0x10, 0, 0)  # Close the acknowledged error dialog.
        assert process.wait(timeout=10) == 1
    if failure != "missing-runtime":
        assert sentinel.read_text(encoding="utf-8") == "preserve existing data"


@pytest.mark.parametrize("minimized", [False, True])
def test_native_resume_recreates_page_and_ignores_old_reply(tmp_path, native, minimized):
    from PIL import ImageGrab

    (native.assets / "index.html").write_text('''<!doctype html>
<style>html,body{margin:0;width:100%;height:100%;background:#e02060}</style>
<div class="titlebar">Resume test</div><script src="bridge.js"></script><script>
window.addEventListener('desktopready', async () => {
  const count = Number(localStorage.getItem('resume-count') || 0) + 1;
  localStorage.setItem('resume-count', count);
  const reply = await desktop.checkpoint(count);
  if (reply !== count) throw Error('reply belongs to a previous page');
  document.body.style.background = '#20d080';
  await desktop.checkpoint('done');
});</script>''', encoding="utf-8")
    with native.launch(tmp_path / "resume-profile") as process:
        messages = queue.Queue()

        def read_messages():
            for line in process.stdout:
                messages.put(json.loads(line))

        reader = threading.Thread(target=read_messages, daemon=True)
        reader.start()
        old = messages.get(timeout=15)
        assert old["args"] == [1]
        handle = wait_for(lambda: native.window(process.pid, "Shared Brain"))
        wait_for(lambda: native.attribute(handle, 14) == 0)
        if minimized:
            native.user.ShowWindow(handle, 6)
            wait_for(lambda: native.user.IsIconic(handle))
        # Deliver the resume notification without suspending the user's computer.
        assert native.user.PostMessageW(handle, 0x218, 0x12, 0)
        if minimized:
            time.sleep(0.2)
            assert messages.empty()  # Keep the hidden page until taskbar restore.
            native.user.ShowWindow(handle, 9)
        current = messages.get(timeout=15)
        assert current["args"] == [2]
        assert current["id"] != old["id"]
        for request, result in ((old, 1), (current, 2)):
            process.stdin.write(json.dumps({"id": request["id"], "result": result}) + "\n")
            process.stdin.flush()
        assert messages.get(timeout=15)["args"] == ["done"]
        wait_for(lambda: native.attribute(handle, 14) == 0)

        def painted():
            frame = ImageGrab.grab(window=handle)
            pixel = frame.getpixel((frame.width // 2, frame.height // 2))[:3]
            return all(abs(a - b) < 12 for a, b in zip(pixel, (32, 208, 128)))

        wait_for(painted)
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        reader.join(timeout=2)
        assert not reader.is_alive()


def test_native_bridge_theme_window_lifecycle_and_restart(tmp_path, native):
    api = DesktopAPI(BrainService(tmp_path / "home"))
    assert not api.initialize(str(tmp_path / "vault")).get("error")
    workspace = tmp_path / "工作目录"
    workspace.mkdir()
    profile = tmp_path / "界面数据"
    (native.assets / "index.html").write_text('''<!doctype html><meta charset="utf-8">
<div class="titlebar">Native integration test</div><script src="bridge.js"></script>
<script>
window.addEventListener('desktopready', async () => {
  const send = value => chrome.webview.postMessage(value);
  try {
    const state = await desktop.status();
    if (!state.ready) throw Error('backend not ready');
    let projects = await desktop.projects();
    if (!projects.projects.length) {
      const result = await desktop.configure_project('原生窗口测试', [WORKSPACE]);
      if (result.error) throw Error(result.error);
    }
    projects = await desktop.projects();
    if (projects.projects.length !== 1) throw Error('project persistence');
    const previous = localStorage.getItem('native-test');
    localStorage.setItem('native-test', 'saved');
    await desktop.checkpoint('loaded', previous);
    for (const command of ['theme:dark', 'theme:light', 'maximize', 'minimize', 'close']) {
      send(command);
      await desktop.checkpoint(command);
    }
    await desktop.checkpoint('done');
  } catch (error) { await desktop.checkpoint('error', error.message); }
});
</script>'''.replace("WORKSPACE", json.dumps(str(workspace))), encoding="utf-8")
    for run in range(2):
        with native.launch(profile) as process:
            messages = queue.Queue()

            def read_messages():
                for line in process.stdout:
                    messages.put(json.loads(line))

            reader = threading.Thread(target=read_messages, daemon=True)
            reader.start()
            while True:
                message = messages.get(timeout=15)
                method, args = message["method"], message["args"]
                if method == "checkpoint":
                    assert args[0] != "error", args
                    if args[0] == "done":
                        break
                    handle = wait_for(lambda: native.window(process.pid, "Shared Brain"))
                    command = args[0]
                    if command == "loaded":
                        assert args[1] == (None if run == 0 else "saved")
                        wait_for(lambda: native.user.IsWindowVisible(handle) and native.attribute(handle, 14) == 0)
                        if sys.getwindowsversion().build >= 22621:
                            assert native.attribute(handle, 38) == 3
                    elif command.startswith("theme:"):
                        if sys.getwindowsversion().build >= 22000:
                            assert native.attribute(handle, 20) == (command == "theme:dark")
                    elif command == "maximize":
                        assert native.user.IsZoomed(handle)
                    elif command == "minimize":
                        assert native.user.IsIconic(handle)
                        native.user.ShowWindow(handle, 9)
                    elif command == "close":
                        assert not native.user.IsWindowVisible(handle)
                        native.user.ShowWindow(handle, 9)
                    result = {}
                else:
                    result = getattr(api, method)(*args)
                process.stdin.write(json.dumps({"id": message["id"], "result": result}) + "\n")
                process.stdin.flush()
            process.stdin.close()  # Normal host shutdown when its parent bridge closes.
            assert process.wait(timeout=10) == 0
            reader.join(timeout=2)
            assert not reader.is_alive()
