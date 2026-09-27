import sys
import threading
from datetime import datetime
from types import SimpleNamespace

from shared_brain import desktop


def test_scheduler_catches_up_after_late_start_and_runs_once(monkeypatch):
    moments = iter(datetime.fromisoformat(v) for v in ["2026-09-01T02:10:00", "2026-09-01T02:10:30", "2026-09-01T02:11:00"])
    monkeypatch.setattr(desktop, "datetime", SimpleNamespace(now=lambda: next(moments)))
    ticks = iter([False] * 3 + [True])
    calls = []
    api = desktop.DesktopAPI(SimpleNamespace(status=lambda: {"ready": True, "pending": 1, "settings": {"maintenance_enabled": True, "maintenance_time": "02:00"}}, maintain=lambda: calls.append(1)))
    desktop._maintenance_loop(api, SimpleNamespace(wait=lambda _: next(ticks)))
    assert calls == [1]


def test_window_close_hides_and_tray_exit_stops_worker(monkeypatch):
    class ClosingEvent:
        def __iadd__(self, callback):
            self.callback = callback
            return self

    closing = ClosingEvent()
    calls = []
    worker_stopped = threading.Event()
    window = SimpleNamespace(
        events=SimpleNamespace(closing=closing),
        hide=lambda: calls.append("hide"),
        show=lambda: calls.append("show"),
        restore=lambda: calls.append("restore"),
        destroy=lambda: calls.append(("destroy", closing.callback())),
    )

    class Icon:
        def __init__(self, *args, menu):
            self.menu = menu
            self.stopped = threading.Event()
            icons.append(self)

        def run(self):
            self.stopped.wait()

        def stop(self):
            self.stopped.set()

    icons = []

    def start():
        assert closing.callback() is False
        icons[0].menu[0].action()
        icons[0].menu[1].action(icons[0], None)

    def worker(api, stop):
        stop.wait()
        worker_stopped.set()

    monkeypatch.setattr(desktop, "BrainService", lambda home: object())
    monkeypatch.setattr(desktop, "_maintenance_loop", worker)
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(create_window=lambda *args, **kwargs: window, start=start))
    monkeypatch.setitem(sys.modules, "pystray", SimpleNamespace(
        Icon=Icon,
        Menu=lambda *items: items,
        MenuItem=lambda label, action, **kwargs: SimpleNamespace(action=action),
    ))
    desktop.run()
    assert calls == ["hide", "show", "restore", ("destroy", True)]
    assert worker_stopped.is_set()
    assert icons[0].stopped.is_set()
