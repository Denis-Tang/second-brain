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


def test_daily_writes_count_added_text_not_net_growth(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 1, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr("shared_brain.vault.now", lambda: clock[0].isoformat())
    vault = Vault(tmp_path / "vault", tmp_path / "app")
    vault.refresh()
    note = vault.save_memory("知识", "甲乙丙丁")
    vault.refresh()
    meta, _ = vault.read(vault.root / note["path"])
    vault.write(note["path"], meta, "甲乙戊己")
    vault.refresh()
    day = clock[0].astimezone().date().isoformat()
    assert vault.overview()["activity"]["days"] == [{"day": day, "tokens": 6}]
    (vault.root / note["path"]).unlink()
    vault.refresh()
    assert vault.overview()["activity"]["days"] == [{"day": day, "tokens": 6}]
    clock[0] += timedelta(days=1)
    vault.save_memory("另一天", "新知识")
    assert vault.overview()["activity"]["days"][-1] == {
        "day": clock[0].astimezone().date().isoformat(), "tokens": 3}


def test_daily_writes_baseline_does_not_backfill_existing_notes(tmp_path):
    vault = Vault(tmp_path / "vault", tmp_path / "app")
    vault.save_memory("原有知识", "旧内容")
    result = vault.overview()
    assert result["activity"]["since"]
    assert result["activity"]["days"] == []
    vault.refresh()
    assert vault.overview()["activity"]["days"] == []
