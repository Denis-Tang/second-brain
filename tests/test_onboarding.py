import json

from shared_brain.onboarding import build_prompt


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
    assert list(tmp_path.iterdir()) == []
