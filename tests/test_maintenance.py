import json
from datetime import datetime, timezone, timedelta
import httpx
import pytest
from shared_brain import maintenance
from shared_brain.errors import ErrorReports
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
    assert metadata["evidence"] == "来源已确认实际结果"
    assert skills[0].relative_to(tmp_path / "vault").parts[1] == "示例"
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
    assert maintenance.reserve(service.settings.home, 1, 30001) is None
    at = maintenance.billing_time()
    with maintenance.ledger(service.settings.home) as con:
        con.execute("INSERT INTO usage(day,month,input,output,cny,settled) VALUES (?,?,?,?,?,1)", ("previous", at.strftime("%Y-%m"), 1, 1, 9.999))
    assert maintenance.reserve(service.settings.home, 1000, 1000) is None
    result = service.maintain()
    assert result["model_calls"] == 0 and len(calls) == 1


def test_maintenance_accepts_long_json_with_ten_thousand_output_tokens(tmp_path, monkeypatch):
    service, context, _ = setup(tmp_path)
    service.save(context, summary="已验证的操作结果", errors_reviewed=True)

    def post(url, **kwargs):
        request = kwargs["json"]
        assert request["max_tokens"] == 10_000
        source = json.loads(request["messages"][1]["content"])["sources"][0]
        note = {"title": "完整经验", "body": "按来源步骤执行并检查实际结果。" * 200, "source_ids": [source["id"]]}
        return httpx.Response(200, json={"usage": {"prompt_tokens": 800, "completion_tokens": 5000},
                                      "choices": [{"message": {"content": json.dumps([note], ensure_ascii=False)}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    result = service.maintain()
    assert result["model_calls"] == 1 and service.status()["pending"] == 0
    assert result["budget"]["today_output"] == 5000
    assert result["budget"]["night_output_limit"] == 30_000


def test_error_scopes_are_maintained_and_searched_separately(tmp_path, monkeypatch):
    service, _, started = setup(tmp_path)
    vault = service._vault()
    reports = ErrorReports(vault)
    reports.save("s1", "Codex", started["project"], [
        {"target": "shared-tool", "method": "project", "symptom": "shared 失败", "attempts": "已尝试"},
        {"target": "shared-tool", "method": "public", "symptom": "shared 失败", "attempts": "已尝试", "global_scope": True},
    ])
    public = vault.save_memory("shared 经验", "shared 公共条件")
    private = vault.save_memory("shared 经验", "shared 项目条件", project=started["project"])
    calls = []
    fake_model(monkeypatch, calls)
    assert service.maintain()["model_calls"] == 2
    assert reports.pending() == []
    assert service.maintain()["model_calls"] == 0
    skills = [path.relative_to(tmp_path / "vault") for path in (tmp_path / "vault" / "技能").rglob("SKILL.md")]
    assert {path.parts[1] for path in skills} == {"示例", "独立项目"}
    for batch in calls:
        entry = next(source for source in batch["sources"] if source["kind"] == "error")
        scope = json.loads(entry["content"])["project"]
        other = public if scope else private
        assert other["path"] not in {note["id"] for note in batch["existing"]}
    assert len(reports.search("shared")) == 2
    assert reports.search("shared", "independent")[0]["project"] == ""
    assert reports.search("shared", started["project"])[0]["project"] == started["project"]
    public_entry = reports.search("shared", "independent")[0]
    reports.feedback("s1", public_entry["error_id"], {"target": "shared-tool", "method": "other", "environment": {}, "evidence": "实际成功"})
    assert [row["project"] for row in reports.pending()] == [""]


def test_object_maintenance_preserves_facts_and_history(tmp_path, monkeypatch):
    service, _, _ = setup(tmp_path)
    vault = service._vault()
    first = {"object": "某模型", "action": "安装", "location": "D:/models/v1", "state": "已安装", "evidence": "成功加载"}
    saved = vault.save_change(first, "Codex", "s1")
    vault.save_change({**first, "action": "迁移", "location": "D:/models/v2", "state": "已迁移", "evidence": "新位置成功加载" + "验证详情。" * 1000}, "Codex", "s2")
    metadata, history = vault.read(vault.root / saved["path"])
    calls = []
    chunks = []

    def post(url, **kwargs):
        material = json.loads(kwargs["json"]["messages"][1]["content"])
        calls.append(material)
        source = material["sources"][0]
        offset = len("".join(chunks))
        assert source["kind"] == "object" and source["content"] == history.rstrip()[offset:offset + 1600]
        chunks.append(source["content"])
        assert source["facts"]["location"] == "D:/models/v2"
        assert len(calls) == 1 or f"第{len(calls) - 1}部分" in source["organized"]
        note = {"title": "某模型", "body": source["organized"] + f"\n迁移后应在新位置实际加载模型。第{len(calls)}部分", "source_ids": [source["id"]], "kind": "object"}
        return httpx.Response(200, json={"usage": {"prompt_tokens": 800, "completion_tokens": 150},
                                      "choices": [{"message": {"content": json.dumps([note])}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    at = maintenance.billing_time()
    assert service.maintain()["model_calls"] == 3
    assert len(vault.pending()) == 1
    monkeypatch.setattr(maintenance, "billing_time", lambda: at + timedelta(days=1))
    assert service.maintain()["model_calls"] == 1
    assert "".join(chunks) == history.rstrip()
    updated, body = vault.read(vault.root / saved["path"])
    assert body.startswith(history.rstrip() + "\n\n## 整理经验\n")
    assert "迁移后应在新位置实际加载模型。" in body
    assert all(updated[key] == metadata[key] for key in ("location", "current_state", "evidence"))
    assert service.maintain()["model_calls"] == 0 and len(calls) == 4


@pytest.mark.parametrize("kind", ["memory", "skill"])
@pytest.mark.parametrize("merge", [False, True])
def test_feedback_review_restores_or_merges_knowledge_without_requeue(tmp_path, monkeypatch, kind, merge):
    service, context, started = setup(tmp_path)
    vault = service._vault()
    reports = ErrorReports(vault)
    reports.save("s1", "Codex", started["project"], [{"target": "login", "method": "HTTP", "environment": {"tool": "v1"}, "symptom": "登录接口超时"}])
    calls = []
    old_path = ""

    def post(url, **kwargs):
        material = json.loads(kwargs["json"]["messages"][1]["content"])
        calls.append(material)
        note = {"title": "登录接口超时旧结论" if len(calls) == 1 else "登录接口超时新结论", "body": "登录接口超时，需要检查连接池并读回结果。",
                "source_ids": [material["sources"][0]["id"]], "kind": kind, "success_evidence": "模拟实际成功依据"}
        if old_path:
            assert next(item for item in material["existing"] if item["id"] == old_path)["kind"] == kind
            note["merge_ids"] = [old_path] if merge else []
        return httpx.Response(200, json={"usage": {"prompt_tokens": 800, "completion_tokens": 150},
                                      "choices": [{"message": {"content": json.dumps([note])}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(maintenance, "NIGHT_CALLS", 1)
    at = maintenance.billing_time()
    assert service.maintain()["model_calls"] == 1
    old_path = vault.search("登录接口超时旧结论", kinds=[kind])[0]["path"]
    unprocessed = vault.save_memory("未完成的登录经验", "登录接口超时的完整处理记录。" * 200, project=started["project"], sources=[str(reports.path("s1"))])["path"]
    error_id = reports.read("s1")["errors"][0]["id"]
    service.feedback(context, "s1", error_id, {"target": "login", "method": "HTTP", "environment": {"tool": "v1"}, "evidence": "连接池修复后读回成功"})
    assert old_path not in {item["path"] for item in vault.search("登录接口超时")}
    # Represents a hidden note already processed in an earlier review batch.
    with vault.connect() as con:
        con.execute("INSERT OR REPLACE INTO processed SELECT path,hash FROM notes WHERE path=?", (old_path,))
    monkeypatch.setattr(maintenance, "billing_time", lambda: at + timedelta(days=1))
    assert service.maintain()["model_calls"] == 1
    metadata, _ = vault.read(vault.root / old_path)
    if merge:
        assert metadata["merged_into"] and metadata["feedback_pending"]
        assert old_path not in {item["path"] for item in vault.search("登录接口超时")}
    else:
        assert metadata["feedback_pending"] is False
        assert old_path in {item["path"] for item in vault.search("登录接口超时")}
    pending = {item["path"]: item for item in vault.pending()}
    assert old_path not in pending and unprocessed in pending
    assert vault.read(vault.root / unprocessed)[0]["feedback_pending"] is False
    assert vault.state("offset:" + unprocessed) == {"hash": pending[unprocessed]["hash"], "offset": 1600}
    assert len(calls) == 2


@pytest.mark.parametrize("same_title", [False, True])
def test_feedback_review_waits_for_complete_error_group(tmp_path, monkeypatch, same_title):
    service, context, started = setup(tmp_path)
    vault = service._vault()
    reports = ErrorReports(vault)
    reports.save("s1", "Codex", started["project"], [
        {"target": f"login-{index}", "method": "HTTP", "environment": {"tool": "v1"}, "symptom": "登录接口超时"}
        for index in range(4)
    ])
    saved = vault.save_memory("登录接口超时旧结论", "登录接口超时需要核对连接池。", project=started["project"], sources=[str(reports.path("s1"))])
    error_id = reports.read("s1")["errors"][0]["id"]
    service.feedback(context, "s1", error_id, {"target": "login-0", "method": "HTTP", "environment": {"tool": "v1"}, "evidence": "实际成功"})
    vault.refresh()
    with vault.connect() as con:
        con.execute("INSERT OR REPLACE INTO processed SELECT path,hash FROM notes WHERE path=?", (saved["path"],))
    calls = []

    def post(url, **kwargs):
        material = json.loads(kwargs["json"]["messages"][1]["content"])
        calls.append(material)
        note = {"title": saved["title"] if same_title else "登录接口复核结论", "body": "登录接口超时需要核对连接池。",
                "source_ids": [material["sources"][0]["id"]]}
        return httpx.Response(200, json={"usage": {"prompt_tokens": 800, "completion_tokens": 150},
                                      "choices": [{"message": {"content": json.dumps([note])}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(maintenance, "NIGHT_CALLS", 1)
    at = maintenance.billing_time()
    for index in range(4):
        monkeypatch.setattr(maintenance, "billing_time", lambda index=index: at + timedelta(days=index))
        assert service.maintain()["model_calls"] == 1
        assert any(item["id"] == saved["path"] for item in calls[-1]["existing"])
        assert vault.read(vault.root / saved["path"])[0]["feedback_pending"] is (index < 3)
        if index < 3:
            group = reports.pending()[0]
            assert vault.state(group["offset_key"])["offset"] == index + 1
            assert saved["path"] not in {item["path"] for item in vault.search("登录接口超时")}
    assert reports.pending() == [] and vault.pending() == []
    assert saved["path"] in {item["path"] for item in vault.search("登录接口超时")}


def test_session_updates_finish_snapshot_then_extract_only_changes(tmp_path, monkeypatch):
    service, context, _ = setup(tmp_path)
    summary = "\n".join(f"step {index:04d} result confirmed." for index in range(370))[:10000]
    saved = service.save(context, summary=summary)["summary"]
    vault = service._vault()
    original = vault.read(vault.root / saved["path"])[1]
    calls = []

    def post(url, **kwargs):
        calls.append(json.loads(kwargs["json"]["messages"][1]["content"]))
        return httpx.Response(200, json={"usage": {"prompt_tokens": 800, "completion_tokens": 10},
                                      "choices": [{"message": {"content": "[]"}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(maintenance, "NIGHT_CALLS", 1)
    at = maintenance.billing_time()
    assert service.maintain()["model_calls"] == 1
    revised = summary + "\n新增结论：连接池调整后实测成功。"
    service.save(context, summary=revised)
    # A new service instance must resume the durable snapshot despite the changed file hash.
    service = type(service)(service.settings.home)
    count = (len(original) + 1599) // 1600
    for day in range(1, count):
        monkeypatch.setattr(maintenance, "billing_time", lambda day=day: at + timedelta(days=day))
        service.save(context, summary=revised)
        assert service.maintain()["model_calls"] == 1
        assert calls[-1]["sources"][0]["content"] == original[day * 1600:(day + 1) * 1600]
    assert "".join(call["sources"][0]["content"] for call in calls) == original
    assert vault.pending()
    monkeypatch.setattr(maintenance, "billing_time", lambda: at + timedelta(days=count))
    assert service.maintain()["model_calls"] == 1
    delta = calls[-1]["sources"][0]["content"]
    assert "+新增结论：连接池调整后实测成功。" in delta and "step 0000" not in delta
    assert len(delta) < 400 and vault.pending() == []
    service.save(context, summary=revised)
    assert service.maintain()["model_calls"] == 0 and vault.pending() == []
    assert len(calls) == count + 1


def test_session_diff_keeps_corrections_deletions_and_progress_after_failure(tmp_path, monkeypatch):
    service, context, _ = setup(tmp_path)
    lines = [f"已保留结果 {index}" for index in range(20)] + ["连接池大小为 20。"]
    saved = service.save(context, summary="\n".join(lines))["summary"]
    vault = service._vault()
    calls = []

    def post(url, **kwargs):
        calls.append(json.loads(kwargs["json"]["messages"][1]["content"]))
        return httpx.Response(200, json={"usage": {"prompt_tokens": 800, "completion_tokens": 10},
                                      "choices": [{"message": {"content": "invalid" if len(calls) == 2 else "[]"}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert service.maintain()["model_calls"] == 1
    processed = vault.state("offset:" + saved["path"])
    revised = "\n".join(line for line in lines[:-1] if line != "已保留结果 8") + "\n连接池大小实测应为 40。"
    service.save(context, summary=revised)
    with pytest.raises(RuntimeError):
        service.maintain()
    assert vault.state("offset:" + saved["path"]) == processed and vault.pending()
    assert service.maintain()["model_calls"] == 1
    delta = calls[-1]["sources"][0]["content"]
    assert delta == calls[-2]["sources"][0]["content"]
    assert "-连接池大小为 20。" in delta and "+连接池大小实测应为 40。" in delta
    assert "-已保留结果 8" in delta and "已保留结果 0\n" not in delta
    assert vault.pending() == [] and vault.read(vault.root / saved["path"])[1].strip() == revised


@pytest.mark.parametrize("kind", ["memory", "skill"])
def test_generated_knowledge_replaces_complete_body_and_preserves_sources(tmp_path, monkeypatch, kind):
    service, context, _ = setup(tmp_path)
    first = service.save(context, summary="pool timeout：执行检查、调整连接池并读回成功。")
    vault = service._vault()
    old_body = "pool timeout：检查连接池。\n" + "调整后必须读回结果。\n" * 8
    revised = "pool timeout：检查连接池；新版增加重试上限，调整后必须读回结果。"
    calls = []

    def post(url, **kwargs):
        material = json.loads(kwargs["json"]["messages"][1]["content"])
        calls.append(material)
        if len(calls) > 1:
            assert next(item for item in material["existing"] if item["kind"] == kind)["body"].strip() == old_body.strip()
        note = {"title": "pool timeout 处理", "body": old_body if len(calls) == 1 else revised,
                "source_ids": [material["sources"][0]["id"]], "kind": kind, "success_evidence": "实际读回成功"}
        return httpx.Response(200, json={"usage": {"prompt_tokens": 800, "completion_tokens": 150},
                                      "choices": [{"message": {"content": json.dumps([note])}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert service.maintain()["model_calls"] == 1
    service.bootstrap(str(tmp_path / "workspace"), "s2", "Codex")
    second = service.save({**context, "session_id": "s2", "root_session_id": "s2"}, summary="pool timeout：新版增加重试上限，实际验证成功。")
    assert service.maintain()["model_calls"] == 1
    result = vault.search("pool timeout", kinds=[kind])
    assert len(result) == 1
    metadata, body = vault.read(vault.root / result[0]["path"])
    assert body.strip() == revised and len(body) < len(old_body)
    assert set(metadata["sources"]) == {first["summary"]["path"], second["summary"]["path"]}
    assert metadata["verified"] is False and metadata["kind"] == kind
    assert service.maintain()["model_calls"] == 0 and vault.pending() == [] and len(calls) == 2


def test_generated_update_without_full_old_body_keeps_note_and_queue(tmp_path, monkeypatch):
    service, context, started = setup(tmp_path)
    vault = service._vault()
    saved = vault.save_memory("pool timeout", "pool timeout 旧条件和步骤。\n" * 500, project=started["project"], generated=True)
    vault.refresh()
    vault.mark_processed(vault.pending(), {})
    original = (vault.root / saved["path"]).read_bytes()
    summary = service.save(context, summary="pool timeout 调整后成功。")

    def post(url, **kwargs):
        material = json.loads(kwargs["json"]["messages"][1]["content"])
        assert saved["path"] not in {item["id"] for item in material["existing"]}
        note = {"title": "pool timeout", "body": "只看到本次片段，无法保留旧条件。", "source_ids": [material["sources"][0]["id"]]}
        return httpx.Response(200, json={"usage": {"prompt_tokens": 800, "completion_tokens": 150},
                                      "choices": [{"message": {"content": json.dumps([note])}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(ValueError, match="完整旧正文"):
        service.maintain()
    assert (vault.root / saved["path"]).read_bytes() == original
    assert [row["path"] for row in vault.pending()] == [summary["summary"]["path"]]
    assert vault.state("offset:" + summary["summary"]["path"]) is None
