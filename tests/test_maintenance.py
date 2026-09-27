import json
from datetime import datetime, timezone, timedelta
import httpx
import pytest
from shared_brain import maintenance
from test_service import setup


def fake_model(monkeypatch, calls, invalid=False):
    def post(url, **kwargs):
        request = kwargs["json"]
        assert request["thinking"] == {"type": "disabled"}
        material = json.loads(request["messages"][1]["content"])
        calls.append(material)
        note = {"title": "可复用步骤", "body": "条件已确认；按来源步骤执行并检查结果。", "source_ids": [material["sources"][0]["id"]], "kind": "skill", "conditions": {"tool": "v1"}, "success_evidence": "来源已确认实际结果"}
        return httpx.Response(200, json={"usage": {"prompt_tokens": 800, "completion_tokens": 150, "prompt_cache_hit_tokens": 200},
                             "choices": [{"message": {"content": "not json" if invalid else json.dumps([note])}}]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)


def test_incremental_maintenance_skills_budget_and_empty_queue(tmp_path, monkeypatch):
    service, context, _ = setup(tmp_path)
    service.save(context, summary="在 v1 环境按顺序执行 A、B，读回目标值成功。", errors_reviewed=True)
    calls = []
    fake_model(monkeypatch, calls)
    result = service.maintain()
    assert result["model_calls"] == 1
    assert service.maintain()["model_calls"] == 0
    assert len(calls) == 1
    skills = list((tmp_path / "vault" / "技能").rglob("SKILL.md"))
    assert len(skills) == 1
    metadata, _ = service._vault().read(skills[0])
    assert metadata["verified"] is False and metadata["sources"]
    assert result["budget"]["used_or_reserved_cny"] > 0
    assert result["budget"]["today_input"] == 800
    assert result["budget"]["unsettled_requests"] == 0
    source = tmp_path / "original.md"
    source.write_text("手写资料", encoding="utf-8")
    service.import_document(str(source))
    assert source.read_text(encoding="utf-8") == "手写资料"


def test_failed_billed_response_and_monthly_daily_caps_persist(tmp_path, monkeypatch):
    service, context, _ = setup(tmp_path)
    service.save(context, summary="有用的新结果", errors_reviewed=True)
    calls = []
    fake_model(monkeypatch, calls, invalid=True)
    with pytest.raises(RuntimeError):
        service.maintain()
    status = maintenance.usage_status(service.settings.home)
    assert status["today_calls"] == 1 and status["used_or_reserved_cny"] > 0
    assert service.status()["pending"] == 1
    assert maintenance.reserve(service.settings.home, 30001, 1) is None
    assert maintenance.reserve(service.settings.home, 1, 4001) is None
    at = maintenance.billing_time()
    with maintenance.ledger(service.settings.home) as con:
        con.execute("INSERT INTO usage(day,month,input,output,cny,settled) VALUES (?,?,?,?,?,1)", ("previous", at.strftime("%Y-%m"), 1, 1, 9.999))
    assert maintenance.reserve(service.settings.home, 1000, 1000) is None
    result = service.maintain()
    assert result["model_calls"] == 0 and len(calls) == 1
