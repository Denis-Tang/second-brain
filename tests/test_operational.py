"""Isolated operational checks: real processes and authoritative Markdown."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Barrier
from types import SimpleNamespace

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from shared_brain.service import BrainService
from shared_brain.vault import Vault


def test_simultaneous_first_refresh_preserves_initial_tracking_date(tmp_path, monkeypatch):
    vault = Vault(tmp_path / "vault", tmp_path / "app")
    original_connect = Vault.connect
    with vault.connect():
        pass
    barrier = Barrier(2)

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            cursor = self.connection.execute(sql, *args)
            if sql == "SELECT value FROM state WHERE key='daily_writes_since'":
                row = cursor.fetchone()
                assert row is None
                barrier.wait(timeout=10)
                return SimpleNamespace(fetchone=lambda: row)
            return cursor

    @contextmanager
    def coordinated_connect(instance):
        with original_connect(instance) as connection:
            yield Connection(connection)

    with monkeypatch.context() as patch:
        patch.setattr(Vault, "connect", coordinated_connect)
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(lambda _: vault.refresh(), range(2)))
    initial = vault.state("daily_writes_since")
    assert initial
    vault.refresh()
    assert vault.state("daily_writes_since") == initial


def test_external_edit_rename_delete_updates_live_search(tmp_path):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    saved = service._vault().save_memory("externalmarker", "beforemarker")
    path = tmp_path / "vault" / saved["path"]
    assert service.search("beforemarker")["results"]
    path.write_text(path.read_text(encoding="utf-8").replace("beforemarker", "aftermarker"), encoding="utf-8")
    assert not service.search("beforemarker")["results"]
    assert service.search("aftermarker")["results"]
    moved = path.with_name("renamed.md")
    path.rename(moved)
    assert service.search("aftermarker")["results"][0]["path"].endswith("renamed.md")
    moved.unlink()
    assert not service.search("aftermarker")["results"]


def test_copied_vault_rebuilds_index_without_old_home(tmp_path):
    original = Vault(tmp_path / "original", tmp_path / "old-home")
    original.save_memory("portablemarker", "authoritative Markdown")
    original.refresh()
    shutil.copytree(original.root, tmp_path / "moved")
    service = BrainService(tmp_path / "fresh-home")
    service.initialize(str(tmp_path / "moved"))
    assert service.search("portablemarker")["results"][0]["text"].strip() == "authoritative Markdown"
    reopened = BrainService(tmp_path / "fresh-home")
    assert reopened.search("portablemarker")["results"]
    assert reopened.status()["counts"]["memories"] == 1


def test_two_writer_processes_keep_distinct_notes(tmp_path):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    vault = service._vault()
    script = """
import sys
from pathlib import Path
from shared_brain.vault import Vault
vault = Vault(Path(sys.argv[1]), Path(sys.argv[2]))
for index in range(12):
    vault.save_memory(f'writer{sys.argv[3]}note{index}', f'process {sys.argv[3]} value {index}')
    vault.refresh()
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    processes = [subprocess.Popen([sys.executable, "-c", script, str(vault.root), str(vault.home), str(worker)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                 for worker in range(2)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=45)
        assert process.returncode == 0, stdout + stderr
    vault.refresh()
    with vault.connect() as con:
        assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 24
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    for worker in range(2):
        for index in range(12):
            assert vault.search(f"writer{worker}note{index}")


def test_source_stdio_save_survives_server_restart(tmp_path):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service.configure_project("stdio", [str(workspace)])
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    params = StdioServerParameters(command=sys.executable,
                                  args=["-m", "shared_brain", "--home", str(service.settings.home), "mcp"], env=env)

    async def exercise():
        for restart in range(2):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    if restart == 0:
                        boot = await session.call_tool("bootstrap", {"cwd": str(workspace), "session_id": "restart", "agent": "Codex"})
                        assert not boot.isError
                        saved = await session.call_tool("save", {"context": {"role": "root", "session_id": "restart", "root_session_id": "restart"},
                                                                 "summary": "stdiorestartmarker", "errors_reviewed": True})
                        assert not saved.isError
                    else:
                        result = await session.call_tool("search", {"query": "stdiorestartmarker"})
                        assert not result.isError
                        assert json.loads(result.content[0].text)["results"]

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))


def test_process_exit_rolls_back_uncommitted_index_update(tmp_path):
    vault = Vault(tmp_path / "vault", tmp_path / "app")
    vault.save_memory("rollbackmarker", "durable Markdown")
    vault.refresh()
    script = """
import os, sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("UPDATE notes SET body='uncommitted corruption'")
os._exit(23)
"""
    result = subprocess.run([sys.executable, "-c", script, str(vault.db_path)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 23
    with vault.connect() as con:
        assert con.execute("SELECT body FROM notes").fetchone()[0].strip() == "durable Markdown"
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert vault.search("rollbackmarker")
