import sys
import threading
import json
import shutil
import subprocess
import pytest
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

from shared_brain import desktop
from shared_brain.service import BrainService
from shared_brain.settings import SettingsStore, default_home


def test_home_outside_appdata_and_preserved_credential_reference(tmp_path, monkeypatch):
    monkeypatch.delenv("SHARED_BRAIN_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "redirected"))
    assert default_home() == tmp_path / ".shared-brain"
    store = SettingsStore()
    store.home.mkdir()
    store.path.write_text(json.dumps({"credential_account": "existing-account", "key_configured": True}))
    assert SettingsStore().account == "existing-account"
    monkeypatch.setenv("SHARED_BRAIN_HOME", str(tmp_path / "explicit"))
    assert default_home() == tmp_path / "explicit"


def test_settings_form_submits_vault_path():
    root = Path(__file__).resolve().parents[1]
    html = (root / "src/shared_brain/web/index.html").read_text(encoding="utf-8")
    form = html.split('<form id="settings-form">')[1].split('</form>')[0]
    assert 'id="vault-path"' in form and 'id="maintenance-time"' in form
    model_card = next(card for card in form.split('<article class="card">')[1:] if '<h2>模型连接</h2>' in card).split('</article>')[0]
    assert 'id="test-button"' in model_card and form.count('id="test-button"') == 1
    script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, {value: '', checked: false, dataset: {},
    classList: {toggle() {}}, addEventListener(event, fn) {this[event] = fn;}});
  return elements.get(id);
};
let submitted;
const settings = {vault_path: 'D:/vault', base_url: 'https://api.deepseek.com', model: 'deepseek-flash', maintenance_time: '03:00'};
const context = {document: {getElementById: get, querySelectorAll: () => []},
  window: {addEventListener() {}, desktop: {
    configure: async values => {submitted = values; return {message: 'saved'};},
    status: async () => ({settings, ready: false})}},
  setTimeout() {}, clearTimeout() {}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
get('vault-path').value = ' D:/vault ';
get('base-url').value = settings.base_url;
get('model').value = settings.model;
get('maintenance-time').value = settings.maintenance_time;
get('settings-form').submit({preventDefault() {}, submitter: null});
assert.equal(submitted.vault_path, 'D:/vault');
'''
    result = subprocess.run([shutil.which("node"), "-e", script, str(root / "src/shared_brain/web/app.js")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_save_settings_persists_vault_and_rejects_file(tmp_path):
    vault = tmp_path / "vault"
    for name in ("项目", "会话总结", "知识", "技能"):
        (vault / name).mkdir(parents=True)
    home = tmp_path / "app"
    api = desktop.DesktopAPI(BrainService(home))
    result = api.configure({"vault_path": str(vault), "maintenance_time": "03:00"})
    assert result["ready"]
    reopened = desktop.DesktopAPI(BrainService(home)).status()
    assert reopened["settings"]["vault_path"] == str(vault.resolve())
    assert reopened["settings"]["maintenance_time"] == "03:00"
    file = tmp_path / "file.txt"
    file.write_text("keep")
    assert "error" in api.configure({"vault_path": str(file)})
    assert api.status()["settings"] == reopened["settings"]


def test_global_prompt_saves_separately_and_reloads_after_vault_switch():
    root = Path(__file__).resolve().parents[1]
    html = (root / "src/shared_brain/web/index.html").read_text(encoding="utf-8")
    overview = html.split('<section id="overview"')[1].split('</section>')[0]
    settings = html.split('<section id="settings"')[1].split('</section>')[0]
    assert 'id="global-prompt-form"' in overview and 'id="global-prompt-form"' not in settings
    assert 'data-copy-unbind disabled>复制解绑提示词</button>' in settings.split('</form>')[-1]
    assert overview.index('class="hint growth-note"') < overview.index('id="global-prompt-form"')
    script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const elements = new Map(), saves = [], configurations = [], copied = [];
const get = id => {
  if (!elements.has(id)) elements.set(id, {value: '', checked: false, dataset: {},
    classList: {toggle() {}}, addEventListener(event, fn) {this[event] = fn;}});
  return elements.get(id);
};
let state = {ready: true, global_prompt: '旧库提示词', settings: {
  vault_path: 'D:/old', base_url: 'https://api.example.com', model: 'example', maintenance_time: '02:00'}};
const copyButtons = ['data-copy-vault', 'data-copy-agent', 'data-copy-unbind'];
const context = {document: {getElementById: get,
  querySelectorAll: selector => copyButtons.filter(name => selector.includes(name)).map(get)},
  window: {addEventListener() {}, desktop: {
    status: async () => state,
    unbind_prompt: async () => ({text: '解绑规则：' + state.settings.vault_path}),
    save_global_prompt: async text => {saves.push(text); return {message: 'saved', global_prompt: text};},
    configure: async values => {
      configurations.push(values);
      state = {ready: true, settings: values, global_prompt: '新库提示词'};
      return {message: 'configured'};
    }}}, navigator: {clipboard: {writeText: async text => copied.push(text)}},
    setTimeout() {}, clearTimeout() {}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await vm.runInContext("activePage = 'settings'; refresh(true)", context);
  assert.equal(get('global-prompt').value, '旧库提示词');
  for (const name of copyButtons) assert.equal(get(name).disabled, false);
  get('data-copy-unbind').click();
  await settle();
  assert.deepEqual(copied, ['解绑规则：D:/old']);
  assert.match(get('toast').textContent, /解绑提示词已复制/);
  get('global-prompt').value = '我编辑的提示词';
  get('global-prompt-form').submit({preventDefault() {}, submitter: get('save-global-prompt')});
  await settle();
  assert.deepEqual(saves, ['我编辑的提示词']);
  assert.equal(configurations.length, 0);
  get('vault-path').value = 'D:/new';
  get('vault-path').input();
  assert.equal(get('global-prompt').disabled, true);
  assert.equal(get('save-global-prompt').disabled, true);
  for (const name of copyButtons) assert.equal(get(name).disabled, true);
  await vm.runInContext('refresh()', context);
  for (const name of copyButtons) assert.equal(get(name).disabled, true);
  get('settings-form').submit({preventDefault() {}, submitter: null});
  await settle();
  assert.equal(configurations[0].vault_path, 'D:/new');
  assert.equal('global_prompt' in configurations[0], false);
  assert.equal(get('global-prompt').value, '新库提示词');
  assert.equal(get('save-global-prompt').disabled, false);
  for (const name of copyButtons) assert.equal(get(name).disabled, false);
  get('data-copy-unbind').click();
  await settle();
  assert.deepEqual(copied, ['解绑规则：D:/old', '解绑规则：D:/new']);
  assert.deepEqual(saves, ['我编辑的提示词']);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    result = subprocess.run([shutil.which("node"), "-e", script, str(root / "src/shared_brain/web/app.js")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_project_editor_separates_creation_editing_and_confirmed_deletion():
    root = Path(__file__).resolve().parents[1]
    script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const elements = new Map(), calls = [], projects = [], deletions = [], copied = [];
const element = () => ({value: '', dataset: {}, children: [], classList: {toggle() {}},
  get lastChild() {return this.children[this.children.length - 1];},
  addEventListener(event, fn) {this[event] = fn;}, append(...items) {this.children.push(...items);},
  replaceChildren() {this.children = [];}, focus() {}, showModal() {this.open = true;}, close() {this.open = false;}});
const get = id => {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);};
const context = {document: {getElementById: get, querySelectorAll: () => [], createElement: element},
  window: {addEventListener() {}, desktop: {
    projects: async () => ({projects}),
    delete_project: async id => {
      deletions.push(id);
      projects.splice(projects.findIndex(p => p.project_id === id), 1);
      return {message: 'deleted'};
    },
    configure_project: async (name, paths, id) => {
      calls.push({name, paths: [...paths], id});
      if (id) projects.find(p => p.project_id === id).name = name;
      else projects.push({project_id: String(projects.length + 1), name, paths, directory: name});
      return {message: 'saved'};
    }}}, navigator: {clipboard: {writeText: async text => copied.push(text)}}, setTimeout() {}, clearTimeout() {}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
vm.runInContext('vaultReady = true', context);
const settle = () => new Promise(resolve => setImmediate(resolve));
const submit = async id => {get('project-form').submit({preventDefault() {}, submitter: get(id)}); await settle();};
(async () => {
  for (const name of ['项目一', '项目二', '项目三']) {
    get('new-project').click();
    assert.equal(get('project-name').value, '');
    assert.equal(get('project-paths').children.length, 0);
    get('project-name').value = name;
    vm.runInContext("projectPaths = ['D:/work']", context);
    await submit('save-project');
    assert.equal(get('project-editor').open, false);
  }
  assert.equal(get('project-count').textContent, '全部项目（3）');
  const card = get('project-list').children[0];
  const actions = card.children[0].children[1], path = card.children[1].children[1];
  assert.equal(actions.children[0].textContent, '编辑');
  assert.equal(actions.children[1].children[0].textContent, '更多');
  assert.equal(card.children[1].children[0].textContent, '工作目录');
  assert.equal(card.children[2].children[0].textContent, '项目资料');
  assert.equal(path.children[0].textContent, 'D:/work');
  assert.equal(path.children[1].textContent, '复制路径');
  path.children[1].click();
  await settle();
  assert.deepEqual(copied, ['D:/work']);
  get('project-list').children[1].children[0].children[1].children[0].click();
  get('project-name').value = '项目二修改';
  await submit('save-project');
  assert.equal(get('project-editor').open, false);
  get('new-project').click();
  get('project-name').value = '项目四';
  await submit('save-project');
  assert.deepEqual(calls.map(c => c.id), ['', '', '', '2', '']);
  assert.deepEqual(projects.map(p => p.name), ['项目一', '项目二修改', '项目三', '项目四']);
  assert.equal(get('project-count').textContent, '全部项目（4）');
  const menu = get('project-list').children[1].children[0].children[1].children[1];
  menu.open = true;
  menu.children[1].lastChild.click();
  await settle();
  assert.equal(get('delete-project-dialog').open, true);
  assert.equal(menu.open, false);
  assert.match(get('delete-project-question').textContent, /项目二修改/);
  assert.deepEqual(deletions, []);
  get('cancel-delete-project').click();
  assert.equal(get('delete-project-dialog').open, false);
  assert.deepEqual(deletions, []);
  get('project-list').children[2].children[0].children[1].children[1].children[1].lastChild.click();
  get('confirm-delete-project').click({currentTarget: get('confirm-delete-project')});
  await settle();
  assert.deepEqual(deletions, ['3']);
  assert.equal(get('delete-project-dialog').open, false);
  assert.deepEqual(projects.map(p => p.name), ['项目一', '项目二修改', '项目四']);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    result = subprocess.run([shutil.which("node"), "-e", script, str(root / "src/shared_brain/web/app.js")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_scheduler_catches_up_after_late_start_and_runs_once(monkeypatch):
    moments = iter(datetime.fromisoformat(v) for v in ["2026-09-01T02:10:00", "2026-09-01T02:10:30", "2026-09-01T02:11:00"])
    monkeypatch.setattr(desktop, "datetime", SimpleNamespace(now=lambda: next(moments)))
    ticks = iter([False] * 3 + [True])
    calls = []
    api = desktop.DesktopAPI(SimpleNamespace(status=lambda: {"ready": True, "pending": 1, "settings": {"maintenance_enabled": True, "maintenance_time": "02:00"}}, maintain=lambda: calls.append(1)))
    desktop._maintenance_loop(api, SimpleNamespace(wait=lambda _: next(ticks)))
    assert calls == [1]


def test_native_host_bridge_stops_worker_on_exit(tmp_path, monkeypatch):
    import io
    stopped = threading.Event()
    reply = io.StringIO()
    class Process:
        def __init__(self, command, **kwargs):
            assert command[0].endswith("SharedBrain.Desktop.exe")
            assert command[-1] == str(tmp_path / "webview")
            self.stdout = io.StringIO('{"id":1,"method":"status","args":[]}\n')
            self.stdin = reply
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    monkeypatch.setattr(desktop.subprocess, "Popen", Process)
    monkeypatch.setattr(desktop, "BrainService", lambda home: SimpleNamespace(settings=SimpleNamespace(home=home), status=lambda: {"ready": False}))
    def worker(api, stop):
        stop.wait()
        stopped.set()
    monkeypatch.setattr(desktop, "_maintenance_loop", worker)
    original_close = reply.close
    reply.close = lambda: None
    desktop._run_desktop(tmp_path)
    assert json.loads(reply.getvalue()) == {"id": 1, "result": {"ready": False}}
    assert stopped.is_set()
    original_close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop singleton")
def test_duplicate_desktop_exits_and_tray_exit_allows_restart(tmp_path, monkeypatch):
    load_library = desktop.ctypes.WinDLL

    def isolated_library(name, **kwargs):
        library = load_library(name, **kwargs)
        if name == "kernel32":
            create_mutex = library.CreateMutexW

            def isolated_mutex(security, owner, name):
                create_mutex.argtypes = isolated_mutex.argtypes
                create_mutex.restype = isolated_mutex.restype
                return create_mutex(security, owner, "Local\\SharedBrain.Test." + tmp_path.parent.name)

            library.CreateMutexW = isolated_mutex
        else:
            library.FindWindowW = lambda *_: None
        return library

    monkeypatch.setattr(desktop.ctypes, "WinDLL", isolated_library)
    calls = []

    def existing(home):
        calls.append(home)
        desktop.run(tmp_path / "another-home")

    monkeypatch.setattr(desktop, "_run_desktop", existing)
    desktop.run()
    assert calls == [None]
    desktop.run()
    assert calls == [None, None]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop activation")
def test_hidden_desktop_window_is_restored_without_starting_another(monkeypatch):
    import ctypes
    from ctypes import wintypes

    user = ctypes.WinDLL("user32")
    user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user.CreateWindowExW.restype = wintypes.HWND
    user.DestroyWindow.argtypes = [wintypes.HWND]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    window = user.CreateWindowExW(0, "STATIC", "Shared Brain", 0x00CF0000, 0, 0, 100, 100,
                                  None, None, None, None)
    assert window
    calls = []
    monkeypatch.setattr(desktop, "_run_desktop", lambda home: calls.append(home))
    try:
        assert not user.IsWindowVisible(window)
        desktop.run()
        assert user.IsWindowVisible(window)
        assert calls == []
    finally:
        user.DestroyWindow(window)
