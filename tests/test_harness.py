import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_service import setup


@pytest.mark.skipif(not shutil.which("node"), reason="Node is needed for the Harness adapter")
def test_harness_root_identity_and_single_closeout_with_real_program(tmp_path):
    service, context, _ = setup(tmp_path)
    root = Path(__file__).resolve().parents[1]
    executable = root / "dist/shared-brain/shared-brain.exe"
    if not executable.exists():
        pytest.skip("Build the Windows distribution to test its Hook process")
    script = r'''
import { apply } from MODULE;
import assert from 'node:assert/strict';
const injected = [], steered = [], warnings = [];
const agent = {session: {header: {id: 's1', cwd: WORKSPACE}},
  inject: (msg) => injected.push(msg), steer: (msg) => steered.push(msg)};
const child = {session: {header: {id: 'child', cwd: WORKSPACE}}};
const handlers = new Map();
apply({agents: {roots: () => [agent]}, on: (name, handler) => handlers.set(name, handler),
  logger: {warn: (msg) => warnings.push(msg)}}, CONFIG);
await handlers.get('agent/created')({agent});
await handlers.get('agent/created')({agent: child});
assert.equal(injected.length, 1);
assert.match(injected[0].content[0].text, /root_session_id/);
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
    script = script.replace("MODULE", json.dumps((root / "integrations/deepseek-harness.mjs").as_uri()))
    script = script.replace("WORKSPACE", json.dumps(str(tmp_path / "workspace")))
    script = script.replace("CONFIG", json.dumps({"command": str(executable), "home": str(service.settings.home)}))
    result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                            capture_output=True, text=True, encoding="utf-8", timeout=25)
    assert result.returncode == 0, result.stderr
    assert "5 checks passed" in result.stdout
