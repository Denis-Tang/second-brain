import json
from pathlib import Path
import pytest
from shared_brain.service import BrainService
from shared_brain.errors import ErrorReports


def setup(tmp_path, choice="new", session="s1"):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    for folder in ("项目", "会话总结", "知识", "技能"):
        (tmp_path / "vault" / folder).mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    started = service.bootstrap(str(workspace), session, "Codex", choice=choice, project_name="示例")
    context = {"role": "root", "session_id": session, "root_session_id": session}
    return service, context, started


def test_workspace_identity_relocation_independent_and_project_progress(tmp_path):
    service, context, first = setup(tmp_path)
    workspace = tmp_path / "workspace"
    second = service.bootstrap(str(workspace), "s2", "Claude")
    assert second["project"] == first["project"]
    saved = service.save(context, summary="阶段一已验证", goal="目标", progress="当前进度", next_actions=["下一步"], errors_reviewed=True)
    updated = service.save(context, summary="阶段一已验证；阶段二完成", progress="完成", next_actions=[], errors_reviewed=True)
    assert updated["summary"]["path"] == saved["summary"]["path"]
    assert saved["summary"]["path"].startswith("会话总结/Codex/")
    assert len(list((tmp_path / "vault" / "会话总结").rglob("*.md"))) == 1
    info = service.bootstrap(str(workspace), "s3", "Codex")["project_context"]
    assert info["progress"].strip() == "完成" and info["next_actions"] == []
    index = tmp_path / "vault" / "项目" / "示例" / "会话索引.md"
    assert index.read_text(encoding="utf-8").count("](") == 1
    moved = tmp_path / "moved"
    workspace.rename(moved)
    unknown = service.bootstrap(str(moved), "s4", "Codex")
    assert unknown["needs_choice"]
    assert service.bootstrap(str(moved), "s4", "Codex", choice="existing", project=first["project"])["project"] == first["project"]
    independent = tmp_path / "independent"
    independent.mkdir()
    assert service.bootstrap(str(independent), "i1", "Codex")["needs_choice"]
    service.bootstrap(str(independent), "i1", "Codex", choice="independent")
    assert service.bootstrap(str(independent), "i2", "Codex")["independent"]
    ctx = {"role": "root", "session_id": "i2", "root_session_id": "i2"}
    service.save(ctx, summary="独立成果", errors_reviewed=True)
    assert not list((tmp_path / "vault" / "项目").rglob("任务"))
    with pytest.raises(ValueError, match="根代理"):
        service.save({**context, "role": "child"}, summary="不能写")
    assert service.status()["counts"]["sessions"] == 2


def test_errors_scope_outcome_feedback_and_separate_storage(tmp_path):
    service, context, started = setup(tmp_path)
    error = {"target": "https://example.com/a?token=private", "method": "HTTP", "environment": {"tool": "v1"}, "symptom": "timeout", "impact": "high"}
    service.save(context, errors=[error])
    service.save(context, errors=[{**error, "attempts": "retried once, timeout"}])
    reports = ErrorReports(service._vault())
    report = reports.read("s1")
    assert len(report["errors"]) == 1
    assert "private" not in reports.path("s1").read_text(encoding="utf-8")
    assert not list((tmp_path / "vault").rglob("*.json"))
    assert service.search("timeout", project="another")["errors"] == []
    assert service.search("timeout", project=started["project"], target="https://example.com/b")["errors"] == []
    found = service.search("timeout", project=started["project"], target="https://example.com/a", environment={"tool": "v1"})["errors"]
    assert found and found[0]["active"]
    error_id = report["errors"][0]["id"]
    workaround = service.feedback(context, "s1", error_id, {"target": "https://example.com/a", "method": "Browser", "environment": {"tool": "v1"}, "evidence": "页面正文已读取"})
    assert workaround["active"] and not workaround["comparable"]
    success = service.feedback(context, "s1", error_id, {"target": "https://example.com/a", "method": "HTTP", "environment": {"tool": "v1"}, "evidence": "请求内容与页面匹配"})
    assert not success["active"] and success["comparable"]
    assert len(reports.read("s1")["errors"][0]["successes"]) == 2


def test_hook_one_reminder_per_user_turn_no_subagent_or_probe_record(tmp_path):
    service, context, _ = setup(tmp_path)
    event = {**context, "event": "closeout"}
    assert service.hook(event)["decision"] == "block"
    assert service.hook(event) == {}
    service.hook({**context, "event": "turn"})
    service.save(context, summary="成果", errors_reviewed=True)
    assert service.hook(event) == {}
    service.hook({**context, "event": "turn"})
    assert service.hook(event)["decision"] == "block"
    assert service.hook(event) == {}
    error = {"target": "sample", "method": "read", "symptom": "not found"}
    assert service.hook({**context, "role": "child", "event": "failure", "actual_failure": True, "error": error}) == {}
    assert service.hook({**context, "event": "failure", "actual_failure": True, "expected_probe": True, "error": error}) == {}
    assert not list((tmp_path / "app").rglob("errors/*.json"))
    assert service.hook({**context, "event": "failure", "actual_failure": True, "error": error}) == {}
    assert len(ErrorReports(service._vault()).read("s1")["errors"]) == 1


def test_first_setup_handwritten_preservation_and_optional_task(tmp_path):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    assert not (tmp_path / "vault").exists()
    assert not service.status()["ready"]
    assert "会话总结/" in service.agent_prompt()["text"]
    service, context, started = setup(tmp_path)
    service.save(context, task_id="cross-session", goal="目标", progress="当前", summary="阶段", errors_reviewed=True,
                 memory={"title": "已验证", "body": "有效步骤", "verified": True, "evidence": "已读回效果"},
                 decision={"title": "重要选择", "body": "背景与理由"}, project_status="paused")
    task = service.bootstrap(str(tmp_path / "workspace"), "s2", "Codex", task_id="cross-session")["task"]
    assert task["progress"].strip() == "当前"
    assert service.search("有效", started["project"])["results"][0]["verified"]
    with pytest.raises(ValueError):
        service.save(context, task_id="../outside")
    assert len(list((tmp_path / "vault" / "项目" / "示例" / "决策").glob("*.md"))) == 1
    for invalid in ("24:00", "2:00", "12:60"):
        with pytest.raises(ValueError):
            service.configure({"maintenance_time": invalid})
