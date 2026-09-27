"""Plain Markdown is authoritative; SQLite supplies search and import progress."""

import hashlib
import json
import re
import sqlite3
import os
import subprocess
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import yaml


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Vault:
    def __init__(self, path: Path, home: Path):
        self.path = path.resolve()
        self.root = self.path
        self.home = home
        self.reports = home / "errors" / fingerprint(str(self.path))[:12]
        self.db_path = home / ("index-" + fingerprint(str(self.path))[:12] + ".sqlite3")

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                path TEXT PRIMARY KEY, title TEXT, kind TEXT, project TEXT,
                verified INTEGER, body TEXT, hash TEXT, metadata TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(path UNINDEXED, text, tokenize='trigram');
            CREATE TABLE IF NOT EXISTS processed (path TEXT PRIMARY KEY, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observations (
                at TEXT NOT NULL, sessions INTEGER, memories INTEGER, estimated_tokens INTEGER
            );
        """)
        try:
            with con:
                yield con
        finally:
            con.close()

    def read(self, path: Path, text: str | None = None) -> tuple[dict, str]:
        if text is None:
            text = path.read_text(encoding="utf-8-sig")
        if not text.startswith("---\n"):
            return {}, text
        parts = text.split("\n---", 1)
        if len(parts) != 2:
            raise ValueError(f"笔记的属性区未闭合：{path.name}")
        try:
            metadata = yaml.safe_load(parts[0][4:]) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"笔记属性格式错误：{path.name}") from exc
        if not isinstance(metadata, dict):
            raise ValueError(f"笔记属性须为键值表：{path.name}")
        return metadata, parts[1].lstrip("\n")

    def write(self, relative: str, metadata: dict, body: str) -> dict:
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("笔记路径必须位于仓库内")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            previous, _ = self.read(path)
            if previous.get("shared_brain") is not True:
                raise ValueError(f"同名文件是用户笔记，未覆盖：{path.name}")
            metadata = {**previous, **metadata}
            if "created" in previous:
                metadata["created"] = previous["created"]
        timestamp = now()
        metadata = dict(metadata, shared_brain=True, updated=timestamp)
        metadata.setdefault("created", timestamp)
        text = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n" + body.rstrip() + "\n"
        path.write_text(text, encoding="utf-8")
        return {"path": path.relative_to(self.path).as_posix(), "title": metadata["title"]}

    def state(self, key: str, value=None):
        with self.connect() as con:
            if value is not None:
                con.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
                return value
            row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def projects(self):
        result = []
        for path in (self.root / "项目").glob("*/项目.md"):
            metadata, body = self.read(path)
            if metadata.get("project_id"):
                result.append({**metadata, "path": path.relative_to(self.root).as_posix(), "body": body})
        return result

    def project(self, project_id):
        return next((p for p in self.projects() if p["project_id"] == project_id), None)

    def workspace(self, cwd: str, workspace_root: str = ""):
        if not cwd.strip():
            raise ValueError("需要宿主启动目录 cwd")
        path = Path(workspace_root or cwd).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("工作区目录不存在")
        if not workspace_root and shutil.which("git"):
            # PyInstaller's DLL directory can break Git's bundled DLLs on Windows.
            bundled_windows = sys.platform == "win32" and getattr(sys, "frozen", False)
            if bundled_windows:
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            try:
                probe = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=5,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            finally:
                if bundled_windows:
                    ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)
            if probe.returncode == 0:
                path = Path(probe.stdout.strip()).resolve()
        return os.path.normcase(str(path))

    def bind(self, workspace: str, choice: str, project_id: str = "", name: str = ""):
        if choice == "new":
            if not name.strip() or len(name) > 100 or re.search(r'[<>:"/\\|?*]', name) or name.rstrip(" .") != name or name in {".", ".."}:
                raise ValueError("请提供有效的项目文件夹名称")
            if (self.root / "项目" / name).exists():
                raise ValueError("项目文件夹已存在，请关联已有项目")
            project_id = uuid4().hex
            self.write(f"项目/{name}/项目.md", {"kind": "project", "title": name, "project": project_id,
                       "project_id": project_id, "status": "active", "goal": "", "next_actions": []}, "尚未保存进度。")
            self.write(f"项目/{name}/会话索引.md", {"kind": "index", "title": "会话索引", "project": project_id}, "")
        elif choice == "existing":
            if not self.project(project_id):
                raise ValueError("未找到已有项目，请从 bootstrap 返回的项目列表选择")
        elif choice == "independent":
            project_id = ""
        else:
            raise ValueError("归属选择须为 new、existing 或 independent")
        return self.state("workspace:" + workspace, {"project_id": project_id, "independent": not project_id})

    def save_task(self, task_id: str, project: str, goal: str, progress: str, next_actions: list[str]):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", task_id):
            raise ValueError("任务 ID 须为 1 到 80 位字母、数字、点、横线或下划线")
        info = self.project(project)
        if not info:
            raise ValueError("独立会话不创建任务卡")
        relative = str(Path(info["path"]).parent / "任务" / (task_id + ".md"))
        return self.write(relative, {"kind": "task", "title": goal or task_id, "task_id": task_id,
                         "project": project, "goal": goal, "next_actions": next_actions}, progress)

    def save_session(self, session_id: str, agent: str, project: str, summary: str, title: str = ""):
        key = "summary:" + fingerprint(session_id)
        relative = self.state(key)
        if not relative:
            if not re.fullmatch(r"[\w-]{1,60}", agent):
                raise ValueError("Agent 名称只能使用文字、数字、下划线和横线")
            date = datetime.fromisoformat(now()).astimezone()
            relative = f"会话总结/{agent}/{date:%Y/%m/%Y-%m-%d}-{fingerprint(session_id)[:20]}.md"
        saved = self.write(relative, {"kind": "session", "title": title or "会话总结", "session_id": session_id,
                           "agent": agent, "project": project}, summary)
        self.state(key, relative)
        if project:
            info = self.project(project)
            index = Path(info["path"]).parent / "会话索引.md"
            metadata, _ = self.read(self.root / index)
            entries = metadata.get("entries", {})
            entries[session_id] = {"path": relative, "title": title or "会话总结", "date": now()[:10]}
            body = "\n".join(f"- {e['date']} [{e['title'].replace('[', '').replace(']', '')}]({Path(os.path.relpath(self.root / e['path'], self.root / index.parent)).as_posix().replace(' ', '%20')})" for e in entries.values())
            self.write(index.as_posix(), {**metadata, "entries": entries}, body)
        return saved

    def save_memory(self, title: str, body: str, project: str = "", verified: bool = False,
                    evidence: str = "", sources: list[str] | None = None, generated: bool = False, kind: str = "memory", conflict: bool = False, conditions: dict | None = None):
        if not title.strip() or not body.strip():
            raise ValueError("经验标题和正文不能为空")
        if verified and not evidence.strip():
            raise ValueError("已验证经验需要填写实际任务结果或用户确认依据")
        identity = fingerprint(project + "\n" + title.strip().casefold())[:20]
        relative = f"技能/{identity}/SKILL.md" if kind == "skill" else f"知识/{identity}.md"
        target = self.root / relative
        existing, previous_body = self.read(target) if target.exists() else ({}, "")
        if generated and previous_body.strip() == body.strip():
            body = previous_body
        elif generated and previous_body:
            body = previous_body.rstrip() + "\n\n" + body
        source_paths = sorted(set((existing.get("sources") or []) + (sources or [])))
        metadata = {"kind": kind, "feedback_pending": False, "merged_into": "", "conflict": conflict or existing.get("conflict", False), "conditions": conditions or existing.get("conditions", {}), "title": title.strip(), "project": project,
                    "verified": verified if not generated else existing.get("verified", False), "evidence": evidence or existing.get("evidence", ""), "sources": source_paths}
        if kind == "skill":
            metadata.update(name=identity, description=title.strip())
        return self.write(relative, metadata, body)

    def import_document(self, source: Path):
        if source.suffix.lower() not in {".md", ".txt"} or not source.is_file():
            raise ValueError("请选择一个 Markdown 或 UTF-8 文本文件")
        if source.stat().st_size > 1024 * 1024:
            raise ValueError("第一版支持不超过 1 MB 的文档，请先按主题拆分")
        body = source.read_text(encoding="utf-8-sig")
        if len(json.dumps(body, ensure_ascii=False)) > 40000:
            raise ValueError("第一版单份导入内容上限为 40,000 字符，请先按主题拆分")
        relative = f"资料/{fingerprint(body)[:20]}.md"
        target = self.root / relative
        if target.exists():
            return {"path": target.relative_to(self.path).as_posix(), "title": source.stem, "unchanged": True}
        target.parent.mkdir(parents=True, exist_ok=True)
        return self.write(relative, {"kind": "source", "title": source.stem, "original_name": source.name}, body)

    def refresh(self):
        with self.connect() as con:
            found = set()
            previous = {row["path"]: row["hash"] for row in con.execute("SELECT path,hash FROM notes")}
            for folder in ("项目", "知识", "资料", "会话总结", "技能"):
                for path in (self.root / folder).rglob("*.md"):
                    relative = path.relative_to(self.path).as_posix()
                    text = path.read_text(encoding="utf-8-sig")
                    digest = fingerprint(text)
                    if previous.get(relative) == digest:
                        found.add(relative)
                        continue
                    metadata, body = self.read(path, text)
                    if metadata.get("shared_brain") is not True:
                        continue
                    found.add(relative)
                    con.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?)", (
                        relative, metadata.get("title", path.stem), metadata.get("kind", "memory"),
                        metadata.get("project", ""), int(metadata.get("verified") is True), body, digest,
                        json.dumps(metadata, ensure_ascii=False, default=str),
                    ))
                    con.execute("DELETE FROM search WHERE path=?", (relative,))
                    con.execute("INSERT INTO search VALUES (?,?)", (relative, metadata.get("title", "") + "\n" + body))
            for path in previous.keys() - found:
                con.execute("DELETE FROM notes WHERE path=?", (path,))
                con.execute("DELETE FROM search WHERE path=?", (path,))
            sessions = memories = tokens = 0
            for row in con.execute("SELECT kind,body FROM notes"):
                sessions += row["kind"] == "session"
                memories += row["kind"] in {"memory", "skill"}
                body = row["body"].strip()
                ascii_count = sum(char.isascii() for char in body)
                tokens += (ascii_count + 3) // 4 + len(body) - ascii_count
            latest = con.execute("SELECT sessions,memories,estimated_tokens FROM observations ORDER BY rowid DESC LIMIT 1").fetchone()
            totals = (sessions, memories, tokens)
            if latest is None or tuple(latest) != totals:
                con.execute("INSERT INTO observations VALUES (?,?,?,?)", (now(), *totals))

    def overview(self, period: str = "24h") -> dict:
        windows = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
        if period not in windows:
            raise ValueError("统计范围须为 24h、7d 或 30d")
        self.refresh()
        end = datetime.fromisoformat(now())
        start = end - windows[period]
        with self.connect() as con:
            earlier = con.execute("SELECT * FROM observations WHERE at<? ORDER BY at DESC,rowid DESC LIMIT 1",
                                  (start.isoformat(timespec="seconds"),)).fetchone()
            rows = con.execute("SELECT * FROM observations WHERE at>=? AND at<=? ORDER BY at,rowid",
                               (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))).fetchall()
            current = dict(con.execute("SELECT * FROM observations ORDER BY rowid DESC LIMIT 1").fetchone())
        # Carry an already observed value to the window edge; never backfill before the baseline.
        first = dict(earlier) if earlier else dict(rows[0])
        if earlier:
            first["at"] = start.isoformat(timespec="seconds")
        samples = [first]
        buckets = {}
        seconds_per_bucket = windows[period].total_seconds() / 119
        for row in rows if earlier else rows[1:]:
            bucket = min(118, int((datetime.fromisoformat(row["at"]) - start).total_seconds() / seconds_per_bucket))
            buckets[bucket] = dict(row)
        samples.extend(buckets.values())
        if samples[-1]["at"] != end.isoformat(timespec="seconds"):
            samples.append({**current, "at": end.isoformat(timespec="seconds")})
        metrics = [("sessions", "会话总结", "篇"), ("memories", "知识（含草稿）", "条"),
                   ("estimated_tokens", "正文估算 token", "tokens")]
        return {"period": period, "metrics": [
            {"key": key, "label": label, "unit": unit, "current": current[key],
             "points": [{"at": sample["at"], "value": sample[key]} for sample in samples]}
            for key, label, unit in metrics
        ]}

    def search(self, query: str, project: str = "", limit: int = 5):
        terms = re.findall(r"\w+", query)[:8]
        if not terms:
            return []
        self.refresh()
        with self.connect() as con:
            if all(len(t) >= 3 for t in terms):
                expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
                rows = con.execute("SELECT n.* FROM search JOIN notes n ON n.path=search.path "
                                   "WHERE search MATCH ? AND n.project IN ('',?) "
                                   "ORDER BY n.verified DESC, (n.kind='memory') DESC, rank LIMIT ?",
                                   (expression, project, limit)).fetchall()
            else:
                clauses = " OR ".join("instr(lower(title || ' ' || body), lower(?))>0" for _ in terms)
                rows = con.execute(f"SELECT * FROM notes WHERE ({clauses}) AND project IN ('',?) "
                                   "ORDER BY verified DESC, (kind='memory') DESC, path LIMIT ?",
                                   (*terms, project, limit)).fetchall()
        result = []
        for row in rows:
            if json.loads(row["metadata"]).get("feedback_pending") or json.loads(row["metadata"]).get("merged_into"):
                continue
            body = row["body"]
            positions = [body.casefold().find(t.casefold()) for t in terms]
            start = max(0, min((p for p in positions if p >= 0), default=0) - 60)
            result.append({"path": row["path"], "title": row["title"][:160], "kind": row["kind"],
                           "verified": bool(row["verified"]), "conflict": json.loads(row["metadata"]).get("conflict", False), "text": body[start:start + 600]})
        return result

    def pending(self):
        self.refresh()
        with self.connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT n.* FROM notes n LEFT JOIN processed p ON n.path=p.path "
                "WHERE n.kind IN ('source','session','memory','skill') AND (p.hash IS NULL OR p.hash != n.hash) "
                "ORDER BY json_extract(n.metadata,'$.created'),n.path")]

    def mark_processed(self, rows: list[dict], receipt: dict):
        with self.connect() as con:
            con.executemany("INSERT OR REPLACE INTO processed VALUES (?,?)", [(r["path"], r["hash"]) for r in rows])
            con.execute("INSERT OR REPLACE INTO state VALUES ('last_maintenance',?)", (json.dumps(receipt, ensure_ascii=False),))
