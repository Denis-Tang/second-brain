import json

from shared_brain.onboarding import build_prompt


def test_prompt_embeds_runtime_paths_and_valid_mcp_json_without_writing(tmp_path):
    vault = tmp_path / "Example Vault"
    config = {
        "mcpServers": {
            "shared-brain": {
                "command": str(tmp_path / "shared brain.exe"),
                "args": ["--home", str(tmp_path / "应用配置"), "mcp"],
            }
        }
    }

    prompt = build_prompt(vault, config)

    assert str(vault) in prompt
    embedded = prompt.split("```json\n", 1)[1].split("```", 1)[0]
    assert json.loads(embedded) == config
    assert list(tmp_path.iterdir()) == []
