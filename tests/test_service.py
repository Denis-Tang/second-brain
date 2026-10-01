import json
from pathlib import Path
import pytest
from shared_brain.service import BrainService
from shared_brain.errors import ErrorReports


def setup(tmp_path, session="s1"):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    for folder in ("项目", "会话总结", "知识", "技能"):
        (tmp_path / "vault" / folder).mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    service.configure_project("示例", [str(workspace)])
    started = service.bootstrap(str(workspace), session, "Codex")
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
    assert unknown["independent"]
    service.configure_project("示例", [str(moved)], first["project"])
    assert service.bootstrap(str(moved), "s4", "Codex")["project"] == first["project"]
    independent = tmp_path / "independent"
    independent.mkdir()
    assert service.bootstrap(str(independent), "i1", "Codex")["independent"]
    assert service.bootstrap(str(independent), "i2", "Codex")["independent"]
    ctx = {"role": "root", "session_id": "i2", "root_session_id": "i2"}
    service.save(ctx, summary="独立成果", errors_reviewed=True)
    assert not list((tmp_path / "vault" / "项目").rglob("任务"))
    with pytest.raises(ValueError, match="根代理"):
        service.save({**context, "role": "child"}, summary="不能写")
    assert service.status()["counts"]["sessions"] == 1


def test_progress_preserves_project_body(tmp_path):
    service, context, started = setup(tmp_path)
    vault = service._vault()
    project = vault.project(started["project"])
    metadata, _ = vault.read(vault.root / project["path"])
    original = "# 项目\n\n## 已有成果\n人工记录\n\n## 原始来源\n[来源](source.md)\n"
    vault.write(project["path"], metadata, original)
    _, original = vault.read(vault.root / project["path"])
    service.save(context, progress="第一次进度")
    service.save(context, progress="最新进度", goal="新目标", next_actions=["复核"])
    service.save(context, project_status="paused")
    metadata, body = vault.read(vault.root / project["path"])
    assert body == original
    assert metadata["progress"] == "最新进度"
    assert metadata["goal"] == "新目标" and metadata["status"] == "paused"
    info = service.bootstrap(str(tmp_path / "workspace"), "another", "Codex")["project_context"]
    assert info["progress"] == "最新进度" and info["notes"] == original[:1600]


def test_projects_newest_first_even_within_one_second_and_after_edit(tmp_path, monkeypatch):
    import shared_brain.vault as vault_module

    timestamps = iter(f"2026-10-01T00:00:00.{microsecond:06d}+00:00" for microsecond in range(1, 30))
    monkeypatch.setattr(vault_module, "now", lambda: next(timestamps))
    service, _, first = setup(tmp_path)
    workspace = tmp_path / "another"
    workspace.mkdir()
    second = service.configure_project("A-最新", [str(workspace)])["project"]
    assert [project["project_id"] for project in service.projects()["projects"]] == [second["project_id"], first["project"]]
    service.configure_project("改名后的旧项目", [], first["project"])
    assert [project["project_id"] for project in BrainService(service.settings.home).projects()["projects"]] == [second["project_id"], first["project"]]


def test_configured_paths_nested_matches_and_live_rebinding(tmp_path):
    service, context, first = setup(tmp_path)
    parent = tmp_path / "workspace"
    nested = parent / "网站"
    child = nested / "src"
    child.mkdir(parents=True)
    other = tmp_path / "文档"
    other.mkdir()
    second = service.configure_project("网站", [str(nested), str(other)])["project"]
    started = service.bootstrap(str(child), "nested", "Claude")
    assert started["project"] == second["project_id"]
    assert Path(started["project_directory"]) == tmp_path / "vault" / "项目" / "网站"
    assert (Path(started["project_directory"]) / "项目.md").is_file()
    assert service.bootstrap(str(other), "other", "Codex")["project"] == second["project_id"]
    assert service.bootstrap(str(child), "explicit", "Codex", workspace_root=str(parent))["project"] == first["project"]
    with pytest.raises(ValueError, match="已属于"):
        service.configure_project("重复", [str(nested / "..")])
    sibling = tmp_path / "workspace-other"
    sibling.mkdir()
    assert service.bootstrap(str(sibling), "sibling", "Codex")["independent"]
    # A separate desktop process changes the configuration while MCP stays alive.
    desktop = BrainService(service.settings.home)
    desktop.configure_project("网站", [], second["project_id"])
    ctx = {"role": "root", "session_id": "nested", "root_session_id": "nested"}
    assert service.save(ctx, summary="改归属后", progress="父项目进度")["project"] == first["project"]
    assert service.bootstrap(str(other), "nested", "Claude")["project"] == first["project"]
    desktop.configure_project("示例", [], first["project"])
    assert service.save(context, summary="已解除绑定")["project"] == ""
    assert service.bootstrap(str(parent), "s1", "Codex")["independent"]


def test_existing_project_configuration_preserves_history_and_ignores_old_bindings(tmp_path):
    service, context, started = setup(tmp_path)
    vault = service._vault()
    saved = service.save(context, summary="历史总结", progress="旧进度")
    summary = vault.root / saved["summary"]["path"]
    original = summary.read_bytes()
    project = vault.project(started["project"])
    metadata, body = vault.read(vault.root / project["path"])
    vault.state("project_paths", {})
    workspace = vault.workspace(str(tmp_path / "workspace"))
    vault.state("workspace:" + workspace, {"project_id": started["project"], "independent": False})
    assert service.bootstrap(workspace, "legacy", "Codex")["independent"]
    old = service.projects()["projects"][0]
    assert old["paths"] == []
    updated = service.configure_project("新显示名称", [workspace], old["project_id"])["project"]
    assert updated["directory"] == old["directory"]
    assert service.bootstrap(workspace, "legacy", "Codex")["project"] == old["project_id"]
    meta, text = vault.read(vault.root / project["path"])
    assert text == body and meta["progress"] == metadata["progress"]
    assert summary.read_bytes() == original


def test_delete_project_only_removes_registration_and_binding(tmp_path):
    service, context, started = setup(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    remaining = service.configure_project("保留项目", [str(other)])["project"]
    saved = service.save(context, summary="历史总结", task_id="task", goal="原目标",
                         progress="已完成步骤", decision={"title": "选择", "body": "理由"})
    vault = service._vault()
    directory = Path(started["project_directory"])
    handwritten = directory / "用户原始资料.md"
    handwritten.write_text("不能删除", encoding="utf-8")
    snapshot = {p: p.read_bytes() for p in directory.rglob("*.md")}
    summary = vault.root / saved["summary"]["path"]
    summary_before = summary.read_bytes()
    service.delete_project(started["project"])
    assert service.projects()["projects"] == [remaining]
    assert started["project"] not in vault.state("project_paths")
    assert service.bootstrap(str(tmp_path / "workspace"), "s1", "Codex")["independent"]
    assert summary.read_bytes() == summary_before
    for path, contents in snapshot.items():
        target = directory / "立项归档.md" if path.name == "项目.md" else path
        assert target.read_bytes() == contents
    assert not (directory / "项目.md").exists()
    assert service.save(context, progress="不能再写回旧项目")["project"] == ""


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
    assert all((tmp_path / "vault" / name).is_dir() for name in ("项目", "会话总结", "知识", "技能"))
    assert service.status()["ready"]
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


def test_global_prompt_scoped_knowledge_objects_and_storage_only_drafts(tmp_path):
    service, context, started = setup(tmp_path)
    vault = service._vault()
    (vault.root / "草稿" / "示例").rmdir()
    service.status()
    assert (vault.root / "草稿" / "示例").is_dir()
    preferences = vault.root / "偏好.md"
    preferences.write_text("代码保持简洁。" * 500, encoding="utf-8")
    service.save_global_prompt(vault.global_prompt() + "\n只由用户修改。")
    assert not preferences.exists()
    assert len(service.bootstrap(str(tmp_path / "workspace"), "s1", "Codex")["global_prompt"]) > 1200
    first = service.save(context, memory={"title": "检索主题 网站", "body": "项目专属方法"})["memory"]
    assert first["path"].startswith("项目/示例/知识/")
    other = tmp_path / "other"
    other.mkdir()
    second = service.configure_project("另一个项目", [str(other)])["project"]
    service.bootstrap(str(other), "other", "Claude")
    ctx = {"role": "root", "session_id": "other", "root_session_id": "other"}
    service.save(ctx, memory={"title": "检索主题 其他", "body": "另一项目方法"})
    saved = service.save(context, changes=[{"object": "检索主题 模型", "action": "安装", "location": "D:/models",
                                          "state": "已安装", "evidence": "加载成功"}])["changes"][0]
    service.save(context, changes=[{"object": "检索主题 模型", "action": "卸载", "location": "D:/models",
                                   "state": "已移除", "evidence": "文件已不存在"}])
    metadata, history = vault.read(vault.root / saved["path"])
    assert metadata["current_state"] == "已移除" and "安装" in history and "卸载" in history
    assert metadata["location"] == "D:/models"
    assert {r["project"] for r in service.search("检索主题")["results"]} == {started["project"], second["project_id"], ""}
    assert {r["project"] for r in service.search("检索主题", started["project"])["results"]} == {started["project"]}
    assert all(not r["project"] for r in service.search("检索主题", "independent")["results"])
    assert service.bootstrap(str(tmp_path / "workspace"), "s1", "Codex", task="检索主题")["relevant"]
    draft = vault.root / "草稿" / "示例" / "想法.md"
    draft.write_text("---\nshared_brain: true\nkind: memory\n---\n检索主题 草稿原文", encoding="utf-8")
    snapshot = draft.read_bytes()
    assert (vault.root / "草稿" / "独立项目").is_dir()
    assert not any(r["path"].startswith("草稿/") for r in service.search("检索主题")["results"])
    assert not any(r["path"].startswith(("草稿/", "知识/")) for r in vault.pending())
    service.configure_project("新显示名称", [str(tmp_path / "workspace")], started["project"])
    assert not (vault.root / "草稿" / "新显示名称").exists()
    service.delete_project(started["project"])
    assert draft.read_bytes() == snapshot


def test_independent_closeout_keeps_failures_but_never_session_documents(tmp_path):
    service, _, started = setup(tmp_path)
    workspace = tmp_path / "standalone"
    workspace.mkdir()
    context = {"role": "root", "session_id": "standalone", "root_session_id": "standalone"}
    service.bootstrap(str(workspace), "standalone", "Codex")
    service.hook({**context, "event": "turn"})
    assert service.hook({**context, "event": "closeout"}) == {}
    service.save(context, summary="不应产生总结", errors_reviewed=True)
    assert not list((tmp_path / "vault" / "会话总结").rglob("*.md"))
    assert not list((tmp_path / "app" / "errors").rglob("*.json"))
    service.hook({**context, "event": "failure", "actual_failure": True,
                  "error": {"target": "model", "method": "load", "symptom": "missing"}})
    reminder = service.hook({**context, "event": "closeout"})
    assert reminder["decision"] == "block" and "不写会话总结" in reminder["reason"]
    service.save(context, errors_reviewed=True)
    assert service.hook({**context, "event": "closeout"}) == {}
    assert service.search("missing")["errors"]
    assert not service.search("missing", started["project"])["errors"]


@pytest.mark.parametrize("query,title", [
    ("修复登录接口的超时问题，然后部署", "登录接口超时"),
    ("接口一直超时怎么排查", "登录接口超时"),
    ("打包时内存不够", "打包内存"),
    ("pool_size登录接口timeout", "登录接口超时"),
    ("login timeout pool_size", "登录接口超时"),
    ("io", "登录接口超时"),
    ("池", "登录接口超时"),
    ("怎么维护㐀㐁㐂", "㐀㐁㐂步骤"),
])
def test_chinese_queries_and_bootstrap_recall(tmp_path, query, title):
    service, _, started = setup(tmp_path)
    vault = service._vault()
    notes = {
        "登录接口超时": "登录接口超时通常因为连接池耗尽，调大 pool_size 后解决。login timeout IO。怎么验证？然后读回。",
        "打包内存": "vite 打包内存不足时设置 NODE_OPTIONS。",
        "㐀㐁㐂步骤": "㐀㐁㐂步骤用于维护。",
    }
    paths = {name: vault.save_memory(name, body, project=started["project"])["path"] for name, body in notes.items()}
    assert paths[title] in {item["path"] for item in service.search(query)["results"]}
    assert service.bootstrap(str(tmp_path / "workspace"), "s1", "Codex", task=query)["relevant"]
    assert service.search("怎么烹饪，然后种菜")["results"] == []
    assert service.search("接口一直渲染怎么处理")["results"] == []
    task = "".join(chr(0x3500 + index) for index in range(480)) + "接口一直超时怎么排查"
    assert paths["登录接口超时"] in {item["path"] for item in vault.search(task, kinds=["memory"])}


def test_chinese_errors_and_search_scopes(tmp_path):
    service, _, started = setup(tmp_path)
    vault = service._vault()
    other = tmp_path / "other"
    other.mkdir()
    second = service.configure_project("其他", [str(other)])["project"]["project_id"]
    reports = ErrorReports(vault)
    for index, project in enumerate((started["project"], "", second)):
        vault.save_memory("登录接口超时", "登录接口超时通常因为连接池耗尽。", project=project)
        reports.save(f"error-{index}", "Codex", project, [{"target": "login", "method": "HTTP", "symptom": "登录接口超时", "impact": "high"}])
    query = "接口一直超时怎么排查"
    for scope, expected in ((started["project"], {started["project"]}), ("independent", {""}), ("all", {started["project"], "", second})):
        result = service.search(query, scope)
        assert {item["project"] for item in result["results"]} == expected
        assert result["errors"] and {item["project"] for item in result["errors"]} <= expected
        assert {item["project"] for item in reports.search(query, scope)} == expected
    assert reports.search("接口一直渲染怎么处理") == []
    assert reports.search("无关问题", started["project"], object_target="login")
    assert vault.search(query, kinds=["skill"]) == []
