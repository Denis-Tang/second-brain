"""Index refresh cost, in-place migration and query routing."""

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from shared_brain.service import BrainService
from shared_brain.vault import Vault, detect_mode, estimate_tokens, search_terms


def make_vault(tmp_path, name="vault"):
    return Vault(tmp_path / name, tmp_path / "app")


def test_child_search_inherits_live_parent_knowledge_without_parent_sessions(tmp_path):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    vault = service._vault()
    parent_work = tmp_path / "parent"
    child_work = tmp_path / "child"
    parent_work.mkdir()
    child_work.mkdir()
    parent = vault.configure_project("主项目", [str(parent_work)])
    child = vault.configure_project("子项目", [str(child_work)], parent_project_id=parent["project_id"])
    paths = []
    for kind in ("memory", "skill", "source", "task", "session"):
        path = f"项目/主项目/{kind}.md"
        vault.write(path, {"kind": kind, "title": kind, "project": parent["project_id"]}, "inheritmarker")
        if kind in {"memory", "skill", "source"}:
            paths.append(path)
    vault.save_memory("public", "inheritmarker")
    inherited = service.search("inheritmarker", child["project_id"])["results"]
    assert {row["path"] for row in inherited} == set(paths)
    assert all(row["project"] == parent["project_id"] for row in inherited)
    assert all(row["project"] == parent["project_id"] for row in service.search("inheritmarker", parent["project_id"])["results"])
    metadata, _ = vault.read(vault.root / paths[0])
    vault.write(paths[0], metadata, "updatedparentmarker")
    assert service.search("updatedparentmarker", child["project_id"])["results"][0]["path"] == paths[0]
    assert len(service.search("inheritmarker", child["project_id"])["results"]) == 2


def test_detect_mode_routes_prose_and_code():
    assert detect_mode("修复登录接口的超时问题") == "text"
    assert detect_mode("how does search work") == "text"
    assert detect_mode("vault.py") == "code"
    assert detect_mode("force_refresh") == "code"
    assert detect_mode("refresh()") == "code"
    assert detect_mode("src/shared_brain/update.py") == "code"
    assert detect_mode("getUserName") == "code"
    assert detect_mode("修复 login 超时") == "text"
    assert detect_mode("refresh()", "text") == "text"
    assert detect_mode("中文查询", "code") == "code"


def test_code_mode_splits_identifiers():
    assert search_terms("forceRefresh", "code") == ["forcerefresh", "force", "refresh"]
    terms = search_terms("READ_TIMEOUT", "code")
    assert "read_timeout" in terms and "read" in terms and "timeout" in terms
    assert search_terms("forceRefresh", "text") == ["forcerefresh"]


def test_search_reports_the_mode_it_used(tmp_path):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    assert service.search("vault.py")["mode"] == "code"
    assert service.search("普通笔记")["mode"] == "text"
    assert service.search("vault.py", mode="text")["mode"] == "text"
    with pytest.raises(ValueError):
        service.search("任意内容", mode="semantic")


def test_index_stores_token_estimates(tmp_path):
    vault = make_vault(tmp_path)
    saved = vault.save_memory("marker", "12345678")
    vault.refresh()
    with vault.connect() as con:
        row = con.execute("SELECT tokens FROM file_state WHERE path=?", (saved["path"],)).fetchone()
    assert row["tokens"] == estimate_tokens("12345678")


def test_source_wikilinks_do_not_enter_search_body_or_token_counts(tmp_path):
    vault = make_vault(tmp_path)
    source = vault.write("资料/linkonlymarker.md", {"kind": "source", "title": "独立来源"}, "原始事实")
    saved = vault.save_memory("结论", "bodymarker", sources=[source["path"]])
    path = vault.root / saved["path"]
    raw = path.read_text(encoding="utf-8")
    assert "[[资料/linkonlymarker]]" in raw
    metadata, body = vault.read(path)
    assert body == "bodymarker\n" and metadata["sources"] == [source["path"]]
    vault.write(saved["path"], metadata, body)
    assert path.read_text(encoding="utf-8").count("<!-- shared-brain:sources -->") == 1
    assert [item["path"] for item in vault.search("linkonlymarker")] == [source["path"]]
    with vault.connect() as con:
        row = con.execute("SELECT n.body,f.tokens FROM notes n JOIN file_state f ON f.path=n.path WHERE n.path=?",
                          (saved["path"],)).fetchone()
    assert row["body"] == body and row["tokens"] == estimate_tokens(body)


def test_refresh_skips_unchanged_files(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    vault.save_memory("first", "body one")
    path = next((vault.root / "项目/独立项目/独立知识").glob("*.md"))
    # Back-date the file so it is outside the racy window, like a long-lived vault entry.
    old = int((time.time() - 5) * 1_000_000_000)
    os.utime(path, ns=(old, old))
    vault.refresh()

    original = Path.read_text
    text = original(path, encoding="utf-8")
    reads = {"count": 0}

    def counted(self, *args, **kwargs):
        reads["count"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    vault.refresh()
    assert reads["count"] == 0, "未变化的文件不应被读取"

    path.write_text(text.replace("body one", "body two"), encoding="utf-8")
    vault.refresh()
    assert reads["count"] == 1, "只有变化的文件需要读取"
    assert vault.search("body two")


def test_refresh_detects_edits_and_deletions(tmp_path):
    vault = make_vault(tmp_path)
    saved = vault.save_memory("marker", "beforemarker")
    vault.refresh()
    path = vault.root / saved["path"]
    path.write_text(path.read_text(encoding="utf-8").replace("beforemarker", "aftermarker"), encoding="utf-8")
    vault.refresh()
    assert not vault.search("beforemarker")
    assert vault.search("aftermarker")
    path.unlink()
    vault.refresh()
    with vault.connect() as con:
        assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 0


def test_refresh_detects_a_same_timestamp_rewrite(tmp_path):
    """Coarse filesystems can hand back an unchanged mtime for a real edit."""
    import os

    vault = make_vault(tmp_path)
    saved = vault.save_memory("marker", "beforemarker")
    vault.refresh()
    path = vault.root / saved["path"]
    with vault.connect() as con:
        recorded = con.execute("SELECT mtime FROM file_state WHERE path=?", (saved["path"],)).fetchone()["mtime"]

    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("beforemarker", "aftermarker"), encoding="utf-8")
    os.utime(path, ns=(recorded, recorded))
    vault.refresh()
    assert not vault.search("beforemarker")
    assert vault.search("aftermarker")


def test_old_index_is_upgraded_in_place(tmp_path):
    vault = make_vault(tmp_path)
    with vault.connect() as con:
        con.execute("INSERT OR REPLACE INTO state VALUES ('project_paths', ?)",
                    (json.dumps({"project-id": ["D:/ws"]}),))
    con = sqlite3.connect(vault.db_path)
    con.executescript("""
        DROP TABLE notes;
        CREATE TABLE notes (path TEXT PRIMARY KEY, title TEXT, kind TEXT, project TEXT,
                            verified INTEGER, body TEXT, hash TEXT, metadata TEXT);
    """)
    con.close()

    saved = vault.save_memory("legacy", "old index body")
    vault.refresh()
    with vault.connect() as con:
        row = con.execute("SELECT mtime,size,tokens FROM file_state WHERE path=?", (saved["path"],)).fetchone()
    assert row["tokens"] == estimate_tokens("old index body")
    assert row["mtime"] and row["size"]
    assert vault.state("project_paths") == {"project-id": ["D:/ws"]}
    assert vault.search("old index body")


def test_migration_keeps_notes_writable_by_older_builds(tmp_path):
    """A shipped build inserts into notes positionally, so notes must keep its original shape."""
    vault = make_vault(tmp_path)
    with vault.connect():
        pass
    con = sqlite3.connect(vault.db_path)
    con.executescript("ALTER TABLE notes ADD COLUMN mtime INTEGER;"
                      "ALTER TABLE notes ADD COLUMN size INTEGER;"
                      "ALTER TABLE notes ADD COLUMN tokens INTEGER;")
    con.close()

    with vault.connect() as con:
        columns = [row[1] for row in con.execute("PRAGMA table_info(notes)")]
        assert columns == ["path", "title", "kind", "project", "verified", "body", "hash", "metadata"]
        con.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?)",
                    ("项目/旧版/笔记.md", "旧版写入", "memory", "", 0, "正文", "hash", "{}"))
        assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM file_state").fetchone()[0] == 0


def test_status_and_pending_do_not_rescan_the_vault(tmp_path, monkeypatch):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    service._vault().save_memory("marker", "pending body")
    service._vault().refresh()

    def boom(self):
        raise AssertionError("status/pending 不应触发全量刷新")

    monkeypatch.setattr(Vault, "refresh", boom)
    state = service.status()
    assert state["ready"] and state["counts"]["memories"] == 1
    assert len(service._vault().pending()) == 1
    assert service._vault().pending_count() == 1


def test_code_query_prefers_name_and_path_matches(tmp_path):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    vault = service._vault()
    vault.save_memory("update.py 说明", "这里说明更新检查模块。")
    vault.save_memory("别的笔记", "正文里顺带提到 update.py 一次。")
    vault.refresh()
    results = service.search("update.py")["results"]
    assert results and results[0]["title"] == "update.py 说明"


def test_search_only_returns_the_requested_rows(tmp_path):
    service = BrainService(tmp_path / "app")
    service.initialize(str(tmp_path / "vault"))
    vault = service._vault()
    for index in range(12):
        vault.save_memory(f"共享记录{index}", f"共享记录第 {index} 条正文。")
    vault.refresh()
    results = vault.search("共享记录", limit=3)
    assert len(results) == 3


def test_search_finds_identifiers_only_in_paths(tmp_path):
    vault = make_vault(tmp_path)
    relative = "项目/sample/知识/readTimeoutGuide.md"
    vault.write(relative, {"kind": "memory", "title": "请求等待说明", "project": "sample"}, "检查等待时间。")
    assert [row["path"] for row in vault.search("readTimeoutGuide", "sample")] == [relative]
    assert not vault.search("readTimeoutGuide", "independent")
    vault.write(relative, {"kind": "memory", "title": "请求等待说明", "project": "sample"}, "调整后的等待时间。")
    assert vault.search("readTimeoutGuide")[0]["text"].strip() == "调整后的等待时间。"


def test_existing_index_gains_paths_without_rereading_notes(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    relative = "项目/sample/知识/readTimeoutGuide.md"
    vault.write(relative, {"kind": "memory", "title": "请求等待说明", "project": "sample"}, "检查等待时间。")
    old = time.time_ns() - 10_000_000_000
    os.utime(vault.root / relative, ns=(old, old))
    vault.refresh()
    vault.state("project_paths", {"sample": ["D:/sample"]})
    with vault.connect() as con:
        con.execute("DELETE FROM state WHERE key='search_includes_path'")
        con.execute("DELETE FROM search")
        con.execute("INSERT INTO search SELECT path,title || char(10) || body FROM notes")
        assert not con.execute("SELECT path FROM search WHERE search MATCH 'readTimeoutGuide'").fetchall()

    def unexpected_read(*args, **kwargs):
        raise AssertionError("索引补入路径不应重新读取未变更笔记")

    monkeypatch.setattr(Path, "read_text", unexpected_read)
    assert vault.search("readTimeoutGuide")[0]["path"] == relative
    assert vault.state("project_paths") == {"sample": ["D:/sample"]}
    assert vault.state("search_includes_path") is True
    assert vault.search("readTimeoutGuide")[0]["path"] == relative
