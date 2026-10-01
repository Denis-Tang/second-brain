import json
import os
import subprocess
from pathlib import Path

import pytest
from test_service import setup


@pytest.mark.parametrize("host", ["Codex", "Claude"])
def test_native_session_identity_and_single_reminder(tmp_path, host):
    service, context, _ = setup(tmp_path)
    root = Path(__file__).resolve().parents[1]
    executable = Path(os.environ.get("SHARED_BRAIN_TEST_EXE", str(root / "dist/shared-brain/shared-brain.exe")))
    if not executable.exists():
        pytest.skip("Build the Windows distribution to test the Hook process")
    node = executable.parent / "runtime/node.exe"
    assert node.is_file() and (executable.parent / "runtime/NODE-LICENSE.txt").is_file()
    assert (executable.parent / "integrations/README.md").is_file()

    def event(name, **extra):
        result = subprocess.run([str(node), str(executable.parent / "integrations/native-hooks.mjs"),
                                 host, str(executable), str(service.settings.home)],
                                input=json.dumps({"hook_event_name": name, "session_id": "s1",
                                                  "cwd": str(tmp_path / "workspace"), **extra}),
                                capture_output=True, text=True, encoding="utf-8", timeout=8,
                                env={**os.environ, "PATH": ""})
        assert result.returncode == 0 and not result.stderr, result.stderr
        return json.loads(result.stdout)

    start = event("SessionStart", source="startup")["hookSpecificOutput"]
    assert start["hookEventName"] == "SessionStart"
    assert '"root_session_id":"s1"' in start["additionalContext"]
    assert "task=当前任务描述" in start["additionalContext"]
    assert "独立会话不写总结" in start["additionalContext"]
    assert "save changes" in start["additionalContext"]
    assert event("SessionStart", agent_id="child") == {}
    assert "子代理" in event("SubagentStart", agent_id="child")["hookSpecificOutput"]["additionalContext"]
    event("UserPromptSubmit")
    assert event("Stop")["decision"] == "block"
    # Codex may deliver the hook continuation as a fresh prompt. Never loop on that continuation.
    event("UserPromptSubmit")
    assert event("Stop", stop_hook_active=True) == {}
    service.save(context, summary="Native hook verified", errors_reviewed=True)
    assert event("Stop") == {}
    event("UserPromptSubmit")
    assert event("Stop")["decision"] == "block"
    assert event("Stop") == {}
