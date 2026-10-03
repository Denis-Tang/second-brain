"""Plain Markdown is authoritative; SQLite supplies search and import progress."""

import hashlib
import json
import re
import sqlite3
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

import yaml


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


CJK = r"\u3400-\u4dbf\u4e00-\u9fff"
CJK_RE = re.compile(f"[{CJK}]")
STOP_TERMS = {"怎么", "然后", "一直"}
# A query reads as code when it looks like an identifier, a path or a file name.
CODE_HINT = re.compile(
    r"[\\/]|::|\w+_\w+|[a-z]+[A-Z]|"
    r"\w+\.(?:py|js|mjs|cjs|ts|tsx|jsx|md|json|toml|ya?ml|ini|cfg|cs|ps1|sh|bat|cmd|exe|zip|sql|html|css|txt)\b|"
    r"\w+\(\)"
)
# Scoring happens in SQL, so a very long task keeps only its useful tail.
MAX_SCORE_TERMS = 128
# A timestamp this close to the previous scan cannot be trusted on coarse filesystems.
RACY_WINDOW_NS = 2_000_000_000


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ASCII counts about four characters per token, CJK one each."""
    body = text.strip()
    ascii_count = sum(char.isascii() for char in body)
    return (ascii_count + 3) // 4 + len(body) - ascii_count


def detect_mode(query: str, mode: str = "auto") -> str:
    """An explicit mode wins; otherwise Chinese reads as prose and code-shaped queries as code."""
    if mode in {"text", "code"}:
        return mode
    if CJK_RE.search(query):
        return "text"
    return "code" if CODE_HINT.search(query) else "text"


def _split_identifier(token: str) -> list[str]:
    """Split camelCase, snake_case and dotted or slashed identifiers into searchable pieces."""
    pieces = []
    for chunk in re.split(r"[_\-.]+", token):
        if chunk:
            pieces.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", chunk) or [chunk])
    return pieces


def search_terms(query: str, mode: str = "text") -> list[str]:
    terms = []
    for token in re.findall(r"\w+", query):
        for part in re.findall(f"[{CJK}]+|[^{CJK}]+", token):
            if CJK_RE.match(part):
                terms.extend(part[index:index + 2] for index in range(max(1, len(part) - 1)))
                continue
            terms.append(part.casefold())
            if mode == "code":
                terms.extend(piece.casefold() for piece in _split_identifier(part) if len(piece) >= 2)
    # Keep the useful tail of a 500-character task, even when every bigram is distinct.
    return list(dict.fromkeys(term for term in terms if term not in STOP_TERMS))[:512]


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
            CREATE TABLE IF NOT EXISTS file_state (
                path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER, tokens INTEGER
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(path UNINDEXED, text, tokenize='trigram');
            CREATE TABLE IF NOT EXISTS processed (path TEXT PRIMARY KEY, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observations (
                at TEXT NOT NULL, sessions INTEGER, memories INTEGER, estimated_tokens INTEGER
            );
            CREATE TABLE IF NOT EXISTS daily_writes (day TEXT PRIMARY KEY, tokens INTEGER NOT NULL);
        """)
        self._migrate(con)
        try:
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def _migrate(con):
        """Change detection lives in its own table so ``notes`` stays writable by older builds.

        Notes rows use positional inserts in shipped versions, so an extra column there would
        break every older copy that shares the same home. A preview build did add those
        columns; drop them again so the index works with both.
        """
        con.execute("CREATE TABLE IF NOT EXISTS file_state "
                    "(path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER, tokens INTEGER)")
        columns = {row[1] for row in con.execute("PRAGMA table_info(notes)")}
        for name in ("mtime", "size", "tokens"):
            if name in columns:
                con.execute(f"ALTER TABLE notes DROP COLUMN {name}")

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

    def global_prompt(self):
        path = self.root / "知识" / "全局提示词.md"
        text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        preferences = self.root / "偏好.md"
        if preferences.exists():
            text = "\n\n".join(part for part in (text.rstrip(), preferences.read_text(encoding="utf-8-sig").rstrip()) if part)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")
            preferences.unlink()
        return text

    def save_global_prompt(self, text: str):
        path = self.root / "知识" / "全局提示词.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def group(self, project: str):
        if not project:
            return "独立项目"
        info = self.project(project)
        if info:
            return Path(info["path"]).parent.name
        for path in (self.root / "项目").glob("*/立项归档.md"):
            metadata, _ = self.read(path)
            if metadata.get("project_id") == project:
                return path.parent.name
        raise ValueError("项目不存在")

    def projects(self):
        result = []
        for path in (self.root / "项目").glob("*/项目.md"):
            metadata, body = self.read(path)
            if metadata.get("project_id"):
                result.append({**metadata, "path": path.relative_to(self.root).as_posix(), "body": body})
        return sorted(result, key=lambda project: project.get("created", ""), reverse=True)

    def project(self, project_id):
        return next((p for p in self.projects() if p["project_id"] == project_id), None)

    def workspace(self, cwd: str, workspace_root: str = ""):
        if not cwd.strip():
            raise ValueError("需要宿主启动目录 cwd")
        path = Path(workspace_root or cwd).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("工作区目录不存在")
        return os.path.normcase(str(path))

    def configured_projects(self):
        paths = self.state("project_paths") or {}
        return [{"project_id": p["project_id"], "name": p["title"],
                 "paths": paths.get(p["project_id"], []),
                 "directory": str(self.root / Path(p["path"]).parent)} for p in self.projects()]

    def match_project(self, workspace: str):
        matches = [(len(Path(root).parts), p["project_id"]) for p in self.configured_projects()
                   for root in p["paths"] if Path(workspace).is_relative_to(Path(root))]
        return max(matches)[1] if matches else ""

    def delete_project(self, project_id: str):
        info = self.project(project_id)
        if not info:
            raise ValueError("项目不存在")
        card = self.root / info["path"]
        archive = card.with_name("立项归档.md")
        if archive.exists():
            raise ValueError("立项归档中已有同名文件，请先整理该文件")
        card.rename(archive)
        mapping = self.state("project_paths") or {}
        mapping.pop(project_id, None)
        self.state("project_paths", mapping)
        self.refresh()

    def configure_project(self, name: str, paths: list[str], project_id: str = ""):
        if not isinstance(name, str) or not name.strip() or len(name) > 100 or re.search(r'[<>:"/\\|?*]', name) or name.rstrip(" .") != name or name in {".", ".."} or Path(name).is_reserved():
            raise ValueError("请提供有效的项目文件夹名称")
        if name == "独立项目":
            raise ValueError("独立项目是公共资料目录，请使用其他项目名称")
        if not isinstance(paths, list) or any(not isinstance(p, str) or not p.strip() or not Path(p).is_absolute() for p in paths):
            raise ValueError("请提供文件夹绝对路径列表")
        normalized = list(dict.fromkeys(os.path.normcase(str(Path(p).expanduser().resolve())) for p in paths))
        mapping = self.state("project_paths") or {}
        info = self.project(project_id) if project_id else None
        if project_id and not info:
            raise ValueError("项目不存在")
        previous = mapping.get(project_id, [])
        for path in normalized:
            if path not in previous and not Path(path).is_dir():
                raise ValueError("工作目录不存在：" + path)
        for item in self.configured_projects():
            if item["project_id"] == project_id:
                continue
            if item["name"] == name:
                raise ValueError("项目名称已存在")
            if set(normalized) & set(item["paths"]):
                raise ValueError("文件夹已属于项目：" + item["name"])
        if not info:
            if not normalized:
                raise ValueError("新增项目至少选择一个文件夹")
            directory = self.root / "项目" / name
            archive = directory / "立项归档.md"
            card = directory / "项目.md"
            if archive.is_file() and not card.exists():
                metadata, _ = self.read(archive)
                if metadata.get("shared_brain") is True and metadata.get("project_id"):
                    project_id = metadata["project_id"]
                    archive.rename(card)
                    info = {"path": card.relative_to(self.root).as_posix()}
            if not info and directory.exists():
                raise ValueError("项目文件夹已存在，请关联已有项目")
        if not info:
            project_id = uuid4().hex
            self.write(f"项目/{name}/项目.md", {"kind": "project", "title": name, "project": project_id,
                       "project_id": project_id, "status": "active", "goal": "", "next_actions": []}, "尚未保存进度。")
            self.write(f"项目/{name}/会话索引.md", {"kind": "index", "title": "会话索引", "project": project_id}, "")
        else:
            metadata, body = self.read(self.root / info["path"])
            self.write(info["path"], {**metadata, "title": name}, body)
        mapping[project_id] = normalized
        self.state("project_paths", mapping)
        (self.root / "草稿" / self.group(project_id)).mkdir(parents=True, exist_ok=True)
        self.refresh()
        return next(p for p in self.configured_projects() if p["project_id"] == project_id)

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
                    evidence: str = "", sources: list[str] | None = None, generated: bool = False, kind: str = "memory", conflict: bool = False, conditions: dict | None = None,
                    existing: list[dict] | None = None):
        if not title.strip() or not body.strip():
            raise ValueError("经验标题和正文不能为空")
        if verified and not evidence.strip():
            raise ValueError("已验证经验需要填写实际任务结果或用户确认依据")
        identity = fingerprint(project + "\n" + title.strip().casefold())[:20]
        group = self.group(project)
        relative = f"技能/{group}/{identity}/SKILL.md" if kind == "skill" else (
            f"项目/{group}/知识/{identity}.md" if project else f"项目/独立项目/独立知识/{identity}.md")
        target = self.root / relative
        previous, previous_body = self.read(target) if target.exists() else ({}, "")
        if generated and previous_body and not any(item["id"] == relative and item["kind"] == kind for item in (existing or [])):
            raise ValueError("同名知识未提供完整旧正文，保留原文与待处理材料；请检查检索范围或正文是否超过维护额度。")
        source_paths = sorted(set((previous.get("sources") or []) + (sources or [])))
        metadata = {"kind": kind, "feedback_pending": False, "merged_into": "", "conflict": conflict or previous.get("conflict", False), "conditions": conditions or previous.get("conditions", {}), "title": title.strip(), "project": project,
                    "verified": verified if not generated else previous.get("verified", False), "evidence": evidence or previous.get("evidence", ""), "sources": source_paths}
        if kind == "skill":
            metadata.update(name=identity, description=title.strip())
        return self.write(relative, metadata, body)

    def save_change(self, change: dict, agent: str, session_id: str):
        title = change["object"].strip()
        identity = fingerprint(title.casefold())[:20]
        relative = f"项目/独立项目/独立知识/{identity}.md"
        path = self.root / relative
        metadata, body = self.read(path) if path.exists() else ({}, "")
        history, separator, organized = body.partition("\n## 整理经验\n")
        entry = (f"### {now()} · {change['action']}\n"
                 f"- 位置：{change['location']}\n- 结果：{change['state']}\n"
                 f"- 验证：{change.get('evidence') or '未验证'}\n- 执行 Agent：{agent}\n")
        history = history.rstrip() + "\n\n" + entry
        body = history + (separator + organized if separator else "")
        metadata.update(kind="object", title=title, project="", location=change["location"],
                        current_state=change["state"], evidence=change.get("evidence", ""),
                        verified=bool(change.get("evidence")), conditions=change.get("conditions", {}),
                        session_id=session_id)
        return self.write(relative, metadata, body)

    def organize_object(self, relative: str, body: str):
        metadata, previous = self.read(self.root / relative)
        history = previous.partition("\n## 整理经验\n")[0].rstrip()
        return self.write(relative, metadata, history + "\n\n## 整理经验\n\n" + body)

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

    def _markdown_files(self):
        """Walk the indexed folders with scandir; only directory metadata is read, never contents."""
        for folder in ("项目", "知识", "资料", "会话总结", "技能"):
            stack = [self.root / folder]
            while stack:
                try:
                    entries = list(os.scandir(stack.pop()))
                except OSError:
                    continue
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                            continue
                        if not entry.name.lower().endswith(".md") or not entry.is_file(follow_symlinks=False):
                            continue
                        path = Path(entry.path)
                        if path == self.root / "知识" / "全局提示词.md":
                            continue
                        stat = entry.stat()
                    except OSError:
                        continue
                    yield path.relative_to(self.path).as_posix(), stat.st_mtime_ns, stat.st_size

    def refresh(self):
        with self.connect() as con:
            started_ns = time.time_ns()
            previous_run = con.execute("SELECT value FROM state WHERE key='refreshed_at'").fetchone()
            racy_before = int(json.loads(previous_run[0])) if previous_run else started_ns + 1
            tracking = con.execute("SELECT value FROM state WHERE key='daily_writes_since'").fetchone()
            day = datetime.fromisoformat(now()).astimezone().date().isoformat()
            added_tokens = 0
            found = set()
            previous = {row["path"]: row for row in
                        con.execute("SELECT n.path,n.hash,f.mtime,f.size,f.tokens FROM notes n "
                                    "LEFT JOIN file_state f ON f.path=n.path")}
            for relative, mtime, size in self._markdown_files():
                old = previous.get(relative)
                # A file written around the previous scan cannot be judged by stat alone,
                # the same reasoning as git's racy-index check.
                racy = mtime + RACY_WINDOW_NS >= racy_before
                if (old is not None and not racy and old["mtime"] == mtime and old["size"] == size
                        and old["tokens"] is not None):
                    found.add(relative)
                    continue
                path = self.root / relative
                try:
                    text = path.read_text(encoding="utf-8-sig")
                except OSError:
                    continue
                digest = fingerprint(text)
                if old is not None and old["hash"] == digest:
                    # Same content, new timestamps: refresh the cheap row only.
                    _, body = self.read(path, text)
                    con.execute("INSERT OR REPLACE INTO file_state VALUES (?,?,?,?)",
                                (relative, mtime, size, estimate_tokens(body)))
                    found.add(relative)
                    continue
                metadata, body = self.read(path, text)
                if metadata.get("shared_brain") is not True:
                    con.execute("DELETE FROM notes WHERE path=?", (relative,))
                    con.execute("DELETE FROM search WHERE path=?", (relative,))
                    con.execute("DELETE FROM file_state WHERE path=?", (relative,))
                    continue
                if tracking:
                    row = con.execute("SELECT body FROM notes WHERE path=?", (relative,)).fetchone()
                    before = row["body"] if row else ""
                    added = "".join(body[j:k] for tag, _, _, j, k in SequenceMatcher(None, before, body).get_opcodes()
                                    if tag in {"insert", "replace"})
                    added_tokens += estimate_tokens(added)
                found.add(relative)
                con.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?)", (
                    relative, metadata.get("title", path.stem), metadata.get("kind", "memory"),
                    metadata.get("project", ""), int(metadata.get("verified") is True), body, digest,
                    json.dumps(metadata, ensure_ascii=False, default=str),
                ))
                con.execute("INSERT OR REPLACE INTO file_state VALUES (?,?,?,?)",
                            (relative, mtime, size, estimate_tokens(body)))
                con.execute("DELETE FROM search WHERE path=?", (relative,))
                con.execute("INSERT INTO search VALUES (?,?)", (relative, relative + "\n" + metadata.get("title", path.stem) + "\n" + body))
            for path in previous.keys() - found:
                con.execute("DELETE FROM notes WHERE path=?", (path,))
                con.execute("DELETE FROM search WHERE path=?", (path,))
                con.execute("DELETE FROM file_state WHERE path=?", (path,))
            if not con.execute("SELECT 1 FROM state WHERE key='search_includes_path'").fetchone():
                con.execute("DELETE FROM search")
                con.execute("INSERT INTO search SELECT path,path || char(10) || title || char(10) || body FROM notes")
                con.execute("INSERT OR IGNORE INTO state VALUES ('search_includes_path','true')")
            con.execute("INSERT OR REPLACE INTO state VALUES ('refreshed_at',?)", (json.dumps(started_ns),))
            sessions = memories = 0
            for row in con.execute("SELECT kind FROM notes"):
                sessions += row["kind"] == "session"
                memories += row["kind"] in {"memory", "skill", "object"}
            tokens = con.execute("SELECT coalesce(sum(f.tokens),0) FROM notes n "
                                 "LEFT JOIN file_state f ON f.path=n.path").fetchone()[0]
            latest = con.execute("SELECT sessions,memories,estimated_tokens FROM observations ORDER BY rowid DESC LIMIT 1").fetchone()
            totals = (sessions, memories, tokens)
            if latest is None or tuple(latest) != totals:
                con.execute("INSERT INTO observations VALUES (?,?,?,?)", (now(), *totals))
            if not tracking:
                con.execute("INSERT OR IGNORE INTO state VALUES ('daily_writes_since',?)", (json.dumps(day),))
            elif added_tokens:
                con.execute("INSERT INTO daily_writes VALUES (?,?) ON CONFLICT(day) DO UPDATE SET tokens=tokens+excluded.tokens",
                            (day, added_tokens))

    def overview(self, period: str = "24h") -> dict:
        windows = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
        if period not in windows:
            raise ValueError("统计范围须为 24h、7d 或 30d")
        self.refresh()
        end = datetime.fromisoformat(now())
        start = end - windows[period]
        with self.connect() as con:
            earlier = con.execute("SELECT * FROM observations WHERE at<? ORDER BY at DESC,rowid DESC LIMIT 1",
                                  (start.isoformat(),)).fetchone()
            rows = con.execute("SELECT * FROM observations WHERE at>=? AND at<=? ORDER BY at,rowid",
                               (start.isoformat(), end.isoformat())).fetchall()
            current = dict(con.execute("SELECT * FROM observations ORDER BY rowid DESC LIMIT 1").fetchone())
            since = json.loads(con.execute("SELECT value FROM state WHERE key='daily_writes_since'").fetchone()[0])
            daily = [dict(row) for row in con.execute("SELECT day,tokens FROM daily_writes ORDER BY day")]
        # Carry an already observed value to the window edge; never backfill before the baseline.
        first = dict(earlier) if earlier else dict(rows[0])
        if earlier:
            first["at"] = start.isoformat()
        samples = [first]
        buckets = {}
        seconds_per_bucket = windows[period].total_seconds() / 119
        for row in rows if earlier else rows[1:]:
            bucket = min(118, int((datetime.fromisoformat(row["at"]) - start).total_seconds() / seconds_per_bucket))
            buckets[bucket] = dict(row)
        samples.extend(buckets.values())
        if samples[-1]["at"] != end.isoformat():
            samples.append({**current, "at": end.isoformat()})
        metrics = [("sessions", "会话总结", "篇"), ("memories", "知识与技能", "条"),
                   ("estimated_tokens", "正文估算 token", "tokens")]
        return {"period": period, "activity": {"since": since, "days": daily}, "metrics": [
            {"key": key, "label": label, "unit": unit, "current": current[key],
             "points": [{"at": sample["at"], "value": sample[key]} for sample in samples]}
            for key, label, unit in metrics
        ]}

    def search(self, query: str, project: str = "", limit: int = 5, kinds=None, mode: str = "auto"):
        """Route the query, score matches in SQLite and return at most ``limit`` rows.

        Scoring never leaves SQLite: only the surviving rows are loaded, so a common term no
        longer pulls every matching body into Python.
        """
        resolved = detect_mode(query, mode)
        terms = search_terms(query, resolved)
        if len(terms) > MAX_SCORE_TERMS:
            # A 500-character task can contribute hundreds of bigrams; the instruction sits at the end.
            terms = terms[-MAX_SCORE_TERMS:]
        if not terms:
            return []
        chinese = bool(CJK_RE.search(query))
        minimum = 2 if chinese and len(terms) >= 3 else 1
        self.refresh()
        scope = "" if project in {"", "all"} else " AND n.project=?"
        parameters = [] if not scope else ["" if project == "independent" else project]
        if kinds:
            scope += " AND n.kind IN (" + ",".join("?" for _ in kinds) + ")"
            parameters += list(kinds)
        title_weight = 4 if resolved == "code" else 3
        path_weight = 3 if resolved == "code" else 1
        score_parts, coverage_parts, values = [], [], []
        for term in terms:
            score_parts.append(f"((instr(lower(n.title), ?) > 0) * {title_weight}"
                               f" + (instr(lower(n.path), ?) > 0) * {path_weight}"
                               " + (instr(lower(n.body), ?) > 0))")
            coverage_parts.append("((instr(lower(n.title), ?) > 0)"
                                  " OR (instr(lower(n.path), ?) > 0)"
                                  " OR (instr(lower(n.body), ?) > 0))")
            values += [term, term, term]
        indexed = not chinese and all(len(term) >= 3 for term in terms)
        sql = (f"SELECT n.path,n.title,n.kind,n.project,n.verified,n.metadata,n.body,"
               f"({'+'.join(score_parts)}) AS score,({'+'.join(coverage_parts)}) AS coverage FROM notes n")
        if indexed:
            expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
            sql += " JOIN search ON search.path=n.path AND search MATCH ?"
        sql += (" WHERE coverage>=?"
                " AND coalesce(json_extract(n.metadata,'$.feedback_pending'),0)!=1"
                " AND coalesce(json_extract(n.metadata,'$.merged_into'),'')=''" + scope +
                " ORDER BY score DESC,coverage DESC,n.verified DESC,(n.kind='memory') DESC,"
                "length(n.body),n.path LIMIT ?")
        arguments = values + values
        if indexed:
            arguments.append(expression)
        arguments += [minimum, *parameters, max(1, limit)]
        with self.connect() as con:
            rows = con.execute(sql, arguments).fetchall()
        result = []
        for row in rows:
            metadata = json.loads(row["metadata"])
            body = row["body"]
            positions = [body.casefold().find(term.casefold()) for term in terms]
            start = max(0, min((p for p in positions if p >= 0), default=0) - 60)
            result.append({"path": row["path"], "title": row["title"][:160], "kind": row["kind"],
                           "project": row["project"], "conditions": metadata.get("conditions", {}),
                           "evidence": metadata.get("evidence", ""), "sources": metadata.get("sources", []),
                           "verified": bool(row["verified"]), "conflict": metadata.get("conflict", False),
                           "text": body[start:start + 600]})
            if row["kind"] == "object":
                result[-1].update(location=metadata.get("location", ""), current_state=metadata.get("current_state", ""))
        return result

    PENDING_MATCH = ("((n.kind='session' AND n.project!='') OR "
                     "(n.kind IN ('memory','object') AND n.path LIKE '项目/%') OR "
                     "(n.kind='skill' AND n.path LIKE '技能/%/%/SKILL.md')) "
                     "AND (p.hash IS NULL OR p.hash != n.hash)")

    def pending(self):
        """Unprocessed material according to the current index; callers refresh when they need it fresh."""
        with self.connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT n.* FROM notes n LEFT JOIN processed p ON n.path=p.path "
                "WHERE " + self.PENDING_MATCH +
                " ORDER BY json_extract(n.metadata,'$.created'),n.path")]

    def pending_count(self):
        """Just the amount of unprocessed material, without loading any note body."""
        with self.connect() as con:
            return con.execute("SELECT count(*) FROM notes n LEFT JOIN processed p ON n.path=p.path "
                               "WHERE " + self.PENDING_MATCH).fetchone()[0]

    def mark_processed(self, rows: list[dict], receipt: dict):
        with self.connect() as con:
            con.executemany("INSERT OR REPLACE INTO processed VALUES (?,?)", [(r["path"], r["hash"]) for r in rows])
            con.execute("INSERT OR REPLACE INTO state VALUES ('last_maintenance',?)", (json.dumps(receipt, ensure_ascii=False),))
