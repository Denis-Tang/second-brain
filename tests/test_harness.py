import json
import os
import subprocess
from pathlib import Path

import pytest

from test_service import setup


@pytest.mark.parametrize("adapter,event", [("deepseek-harness", "agent/created"), ("oh-dsh", "agent/session-start")])
def test_harness_root_identity_and_single_closeout_with_real_program(tmp_path, adapter, event):
    service, context, _ = setup(tmp_path)
    root = Path(__file__).resolve().parents[1]
    executable = Path(os.environ.get("SHARED_BRAIN_TEST_EXE", str(root / "dist/shared-brain/shared-brain.exe")))
    if not executable.exists():
        pytest.skip("Build the Windows distribution to test its Hook process")
    node = executable.parent / "runtime/node.exe"
    script = r'''
import { apply } from MODULE;
import assert from 'node:assert/strict';
const injected = [], steered = [], warnings = [];
const agent = {session: {id: 's1', header: {id: 's1', cwd: WORKSPACE}},
  inject: (msg) => injected.push(msg), steer: (msg) => steered.push(msg)};
const child = {session: {header: {id: 'child', cwd: WORKSPACE}}};
const handlers = new Map();
apply({agents: {roots: () => [agent]}, on: (name, handler) => handlers.set(name, handler),
  logger: {warn: (msg) => warnings.push(msg)}}, CONFIG);
await handlers.get(EVENT)({agent});
await handlers.get(EVENT)({agent: child});
assert.equal(injected.length, 1);
assert.match(injected[0].content[0].text, /root_session_id/);
assert.match(injected[0].content[0].text, /task=当前任务描述/);
assert.match(injected[0].content[0].text, /独立会话也 save 完整 summary/);
assert.match(injected[0].content[0].text, /save changes/);
const preStep = handlers.get('agent/pre-step'), stop = handlers.get('agent/turn-stopping');
await preStep({agent, messages: [{id: 'user-one', source: {kind: 'user'}}]}, async () => ({kind: 'enter'}));
await stop({agent});
assert.equal(steered.length, 1);
await preStep({agent, messages: [steered[0]]}, async () => ({kind: 'enter'}));
await stop({agent});
await stop({agent: child});
assert.equal(steered.length, 1);
await preStep({agent, messages: [{id: 'user-two', source: {kind: 'user'}}]}, async () => ({kind: 'enter'}));
await stop({agent});
assert.equal(steered.length, 2);
assert.equal(warnings.length, 0);
console.log('Harness adapter: 5 checks passed');
'''
    script = script.replace("MODULE", json.dumps((executable.parent / f"integrations/{adapter}.mjs").as_uri()))
    script = script.replace("EVENT", json.dumps(event))
    script = script.replace("WORKSPACE", json.dumps(str(tmp_path / "workspace")))
    script = script.replace("CONFIG", json.dumps({"command": str(executable), "home": str(service.settings.home)}))
    result = subprocess.run([str(node), "--input-type=module", "-e", script],
                            capture_output=True, text=True, encoding="utf-8", timeout=25,
                            env={**os.environ, "PATH": ""})
    assert result.returncode == 0, result.stderr
    assert "5 checks passed" in result.stdout
