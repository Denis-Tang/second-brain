import json

from shared_brain.onboarding import build_prompt
from shared_brain.service import BrainService


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
    assert "独立会话不写总结" in prompt and "save(changes=[...])" in prompt
    assert "草稿不自动加载、检索或夜间整理" in prompt
    assert "不要重复初始化、重建 Obsidian 架构" in prompt
    assert "已有相同 shared_brain 接入则复用" in prompt
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
