import json
from pathlib import Path

from shared_brain.onboarding import build_prompt
from shared_brain.service import BrainService
from shared_brain.desktop import DesktopAPI


def test_prompt_embeds_runtime_paths_and_valid_mcp_json_without_writing(tmp_path):
    vault = tmp_path / "Example Vault"
    config = {
        "mcpServers": {
            "shared_brain": {
                "command": str(tmp_path / "shared brain.exe"),
                "args": ["--home", str(tmp_path / "应用配置"), "mcp"],
            }
        }
    }

    prompt = build_prompt(vault, config)

    assert str(vault) in prompt
    embedded = prompt.split("```json\n", 1)[1].split("```", 1)[0]
    assert json.loads(embedded) == config
    assert str(tmp_path / "integrations" / "README.md") in prompt
    assert str(tmp_path / "runtime" / "node.exe") in prompt
    assert "native-hooks.mjs" in prompt and "已有适配优先使用" in prompt
    assert "task=当前任务描述" in prompt
    assert 'project="independent"' in prompt and 'project="all"' in prompt
    assert "独立会话也保存完整总结" in prompt and "save(changes=[...])" in prompt
    assert "草稿不自动加载、检索或夜间整理" in prompt
    assert "不要重复初始化、重建 Obsidian 架构" in prompt
    assert "已有相同 shared_brain 接入则复用" in prompt
    checks = prompt.rsplit("配置完成后的 Hook 检查（真实故障经验）：", 1)[1]
    assert all(text in checks for text in ('& "<安装目录>/runtime/node.exe"', "hooks/list",
                                          "trustStatus", "自动注入根身份", "不把手动运行当作自动触发证据"))
    assert prompt.rstrip().endswith("尚未观测自动触发的事件明确列为待验证，报告实际配置位置、验证结果及剩余问题。")
    assert list(tmp_path.iterdir()) == []


def test_agent_prompt_reuses_saved_vault_and_updates_after_switch(tmp_path):
    service = BrainService(tmp_path / "app")
    first = tmp_path / "first"
    second = tmp_path / "second"
    service.initialize(str(first))
    note = first / "用户笔记.md"
    note.write_text("已有架构和资料", encoding="utf-8")
    before = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    prompt = service.agent_prompt()["text"]
    assert prompt == BrainService(service.settings.home).agent_prompt()["text"]
    assert f"Vault 绝对路径：{first.resolve()}" in prompt
    assert {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()} == before
    service.configure({"vault_path": str(second)})
    prompt = service.agent_prompt()["text"]
    assert f"Vault 绝对路径：{second.resolve()}" in prompt
    assert str(first.resolve()) not in prompt


def test_unbind_prompt_keeps_materials_and_uses_current_project_mapping(tmp_path):
    service = BrainService(tmp_path / "app")
    api = DesktopAPI(service)
    assert "error" in api.unbind_prompt()
    first = tmp_path / "知识库"
    workspace = tmp_path / "工作目录"
    workspace.mkdir()
    service.initialize(str(first))
    service.configure_project("解绑项目", [str(workspace)])
    note = first / "用户笔记.md"
    note.write_text("用户资料保持原样", encoding="utf-8")
    before = {p: p.read_bytes() for folder in (first, service.settings.home)
              for p in folder.rglob("*") if p.is_file()}
    prompt = api.unbind_prompt()["text"]
    configs = [json.loads(block.split("```", 1)[0]) for block in prompt.split("```json\n")[1:]]
    assert configs[0] == service.mcp_config()
    assert configs[1] == service.projects()["projects"]
    assert str(first.resolve()) in prompt and str(service.settings.home.resolve()) in prompt
    assert [Path(path) for path in configs[1][0]["paths"]] == [workspace.resolve()]
    assert Path(configs[1][0]["directory"]) == first / "项目" / "解绑项目"
    assert all(text in prompt for text in ("merged_into", "feedback_pending", "草稿", "errors",
                                          "entries", "codex mcp remove shared_brain", "保留其他 MCP"))
    assert {p: p.read_bytes() for folder in (first, service.settings.home)
            for p in folder.rglob("*") if p.is_file()} == before
    second = tmp_path / "另一知识库"
    service.initialize(str(second))
    prompt = api.unbind_prompt()["text"]
    assert str(second.resolve()) in prompt and str(first.resolve()) not in prompt
    assert "解绑项目" not in prompt
