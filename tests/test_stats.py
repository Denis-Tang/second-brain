from datetime import datetime, timedelta, timezone
from shared_brain.vault import Vault


def test_summary_updates_preserve_count_and_chart_ranges(tmp_path, monkeypatch):
    clock = [datetime(2026, 9, 26, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr("shared_brain.vault.now", lambda: clock[0].isoformat(timespec="seconds"))
    vault = Vault(tmp_path / "vault", tmp_path / "app")
    vault.save_session("one", "Codex", "", "中文")
    baseline = clock[0].isoformat(timespec="seconds")
    vault.refresh()
    clock[0] += timedelta(hours=2)
    vault.save_session("one", "Codex", "", "中文更多内容")
    vault.save_memory("知识", "有效经验")
    for period in ("24h", "7d", "30d"):
        metrics = {m["key"]: m for m in vault.overview(period)["metrics"]}
        assert metrics["sessions"]["current"] == 1
        assert metrics["memories"]["current"] == 1
        assert metrics["estimated_tokens"]["current"] == 10
        assert metrics["sessions"]["points"][0]["at"] == baseline
        assert metrics["estimated_tokens"]["points"][0]["value"] == 2
