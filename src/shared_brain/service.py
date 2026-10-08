"""One application core shared by the desktop interface and MCP."""

import json
import os
import sys
import webbrowser
from dataclasses import asdict
from pathlib import Path

from . import __version__, installer, update
from .model import ModelClient
from .onboarding import build_prompt, build_unbind_prompt, build_update_prompt
from .settings import SettingsStore
from .vault import Vault, now, fingerprint, detect_mode
from .errors import ErrorReports
from .maintenance import maintain, usage_status


class BrainService:
    def __init__(self, home: Path | None = None):
        self.settings = SettingsStore(home)

    def _vault(self) -> Vault:
        settings = self.settings.load()
        if not settings.vault_path:
            raise ValueError("请先创建或连接一个 Obsidian 知识库")
        vault = Vault(Path(settings.vault_path), self.settings.home)
        for folder in ("项目/独立项目/独立知识", "会话总结", "知识", "技能", "草稿/独立项目"):
            (vault.root / folder).mkdir(parents=True, exist_ok=True)
        for project in vault.projects():
            (vault.root / "草稿" / Path(project["path"]).parent.name).mkdir(parents=True, exist_ok=True)
        return vault

    def status(self) -> dict:
        settings = self.settings.load()
        values = asdict(settings)
        configured = values.pop("key_configured")
        values.pop("credential_account")
        result = {"version": __version__, "settings": values, "key_configured": configured, "ready": False, "global_prompt": "",
                  "counts": {"tasks": 0, "sessions": 0, "memories": 0, "sources": 0}, "pending": 0, "last_maintenance": None,
                  "connections": []}
        if settings.vault_path and all((Path(settings.vault_path) / folder).is_dir()
                                       for folder in ("项目", "会话总结", "知识", "技能")):
            vault = self._vault()
            result["global_prompt"] = vault.global_prompt()
            result["ready"] = True
            result["pending"] = vault.pending_count() + len(ErrorReports(vault).pending())
            with vault.connect() as con:
                counts = {r[0]: r[1] for r in con.execute("SELECT kind,count(*) FROM notes GROUP BY kind")}
                result["counts"] = {"tasks": counts.get("task", 0), "memories": counts.get("memory", 0) + counts.get("skill", 0) + counts.get("object", 0),
                                    "sources": counts.get("source", 0), "sessions": counts.get("session", 0)}
                row = con.execute("SELECT value FROM state WHERE key='last_maintenance'").fetchone()
                result["last_maintenance"] = json.loads(row[0]) if row else None
                result["connections"] = [json.loads(row[0]) for row in con.execute(
                    "SELECT value FROM state WHERE key LIKE 'connection:%' ORDER BY json_extract(value,'$.last_seen') DESC")]
        result["budget"] = usage_status(self.settings.home)
        return result

    def initialize(self, vault_path: str) -> dict:
        settings = self.settings.update({"vault_path": vault_path})
        self._vault().refresh()
        path = settings.vault_path
        return {"message": "知识库已就绪，可配置项目并复制提示词接入 Agent", "path": str(path)}

    def configure(self, values: dict, api_key: str | None = None) -> dict:
        self.settings.update(values, api_key)
        if self.settings.load().vault_path:
            self._vault().refresh()
        return {"message": "设置已保存", **self.status()}

    def save_global_prompt(self, text: str) -> dict:
        if not isinstance(text, str):
            raise ValueError("全局提示词须为文本")
        vault = self._vault()
        vault.global_prompt()
        vault.save_global_prompt(text)
        return {"message": "全局提示词已保存", "global_prompt": text}

    def projects(self) -> dict:
        return {"projects": self._vault().configured_projects()}

    def configure_project(self, name: str, paths: list[str], project_id: str = "", parent_project_id: str = "") -> dict:
        return {"project": self._vault().configure_project(name, paths, project_id, parent_project_id), "message": "项目配置已保存"}

    def project_changes(self, project_id: str) -> dict:
        return self._vault().project_changes(project_id)

    def create_commit(self, project_id: str, item_ids: list[str], message: str = "") -> dict:
        return {"commit": self._vault().create_commit(project_id, item_ids, message), "message": "成果已提交"}

    def project_commits(self, project_id: str) -> dict:
        return {"commits": self._vault().project_commits(project_id)}

    def project_commit(self, project_id: str, commit_id: str) -> dict:
        return self._vault().project_commit(project_id, commit_id)

    def merge_commit(self, project_id: str, commit_id: str, resolutions: dict | None = None) -> dict:
        return self._vault().merge_commit(project_id, commit_id, resolutions)

    def documents(self, project: str = "", query: str = "") -> dict:
        vault = self._vault()
        vault.refresh()
        scope, params = ("", []) if project in {"", "all"} else (" AND project=?", ["" if project == "independent" else project])
        if query.strip():
            scope += " AND instr(lower(title || ' ' || body),lower(?))>0"
            params.append(query.strip())
        with vault.connect() as con:
            rows = con.execute("SELECT path,title,kind,project,json_extract(metadata,'$.updated') AS updated FROM notes "
                                "WHERE kind IN ('session','memory','skill','task','decision','source','object') "
                                "AND coalesce(json_extract(metadata,'$.merged_into'),'')='' "
                                "AND coalesce(json_extract(metadata,'$.feedback_pending'),0)!=1" + scope +
                               " ORDER BY updated DESC,path LIMIT 200", params).fetchall()
        return {"documents": [dict(row) for row in rows]}

    def document(self, relative: str) -> dict:
        vault = self._vault()
        path = self._document_path(vault, relative)
        relative = path.relative_to(vault.root).as_posix()
        metadata, body = vault.read(path)
        vault.refresh()
        with vault.connect() as con:
            sources = [dict(row) for row in con.execute(
                "SELECT path,title,kind FROM notes WHERE path IN (SELECT value FROM json_each(?)) ORDER BY title",
                (json.dumps(metadata.get("sources", [])),))]
            backlinks = [dict(row) for row in con.execute(
                "SELECT DISTINCT n.path,n.title,n.kind FROM notes n,json_each(n.metadata,'$.sources') s "
                "WHERE s.value=? AND coalesce(json_extract(n.metadata,'$.merged_into'),'')='' "
                "AND coalesce(json_extract(n.metadata,'$.feedback_pending'),0)!=1 ORDER BY n.title", (relative,))]
        return {"path": relative, "title": metadata.get("title", path.stem), "kind": metadata.get("kind", "source"),
                "body": body, "sources": sources, "backlinks": backlinks}

    @staticmethod
    def _document_path(vault, relative):
        path = (vault.root / relative).resolve()
        if not path.is_relative_to(vault.root) or path.suffix.lower() != ".md" or "草稿" in path.relative_to(vault.root).parts:
            raise ValueError("请选择知识库中的正文文档")
        return path

    def open_document(self, relative: str) -> dict:
        path = self._document_path(self._vault(), relative)
        if not path.is_file():
            raise ValueError("原文已不存在")
        os.startfile(path)
        return {"opened": True}

    def delete_project(self, project_id: str) -> dict:
        self._vault().delete_project(project_id)
        return {"message": "项目立项已删除，历史资料已保留"}

    def agent_prompt(self) -> dict:
        path = self.settings.load().vault_path
        if not path:
            raise ValueError("请先选择并保存仓库路径")
        return {"text": build_prompt(Path(path), self.mcp_config())}

    def unbind_prompt(self) -> dict:
        vault = self._vault()
        return {"text": build_unbind_prompt(vault.path, self.settings.home.resolve(),
                                           self.mcp_config(), vault.configured_projects())}

    def installation_directory(self) -> Path | None:
        """Directory holding the running program; None when it runs from source."""
        command = self.mcp_config()["mcpServers"]["shared_brain"]
        return None if "-m" in command["args"] else Path(command["command"]).parent

    def check_update(self, force: bool = True) -> dict:
        """Ask GitHub for the newest release; only reads, never downloads or installs."""
        result = update.check_latest(self.settings.home, force=force)
        install = self.installation_directory()
        result["install_directory"] = str(install) if install else ""
        result["prompt"] = ""
        result["local_archive"] = ""
        result["local_verified_at"] = ""
        if result["newer"]:
            staged = update.verified_archive(self.settings.home, result["latest"])
            digest = str((result["asset"] or {}).get("sha256") or "").lower()
            if staged and digest and str(staged.get("sha256") or "").lower() == digest:
                result["local_archive"] = str(staged.get("path") or "")
                result["local_verified_at"] = str(staged.get("verified_at") or "")
            release = {"version": result["latest"], "asset": result["asset"], "page": result["page"]}
            result["prompt"] = build_update_prompt(install, self.settings.home.resolve(), self.mcp_config(),
                                                   result["current"], release, local_archive=result["local_archive"])
        return result

    def open_release_page(self) -> dict:
        """Open the public release page in the default browser."""
        url = update.release_page()
        try:
            opened = webbrowser.open(url)
        except Exception:  # noqa: BLE001 - a missing browser must not break the window
            opened = False
        return {"opened": bool(opened), "url": url}

    def apply_update(self) -> dict:
        """Stage the verified release; a detached script swaps it once this program exits."""
        install = self.installation_directory()
        if install is None:
            return {"started": False, "message": "源码运行时不支持自动替换，请把更新提示词交给 Agent 处理。"}
        release = self.check_update()
        if not release.get("newer"):
            return {"started": False, "message": release.get("message") or "没有可更新的版本。"}
        staged = update.verified_archive(self.settings.home, release["latest"])
        digest = str((release.get("asset") or {}).get("sha256") or "")
        if not staged or not digest or str(staged.get("sha256") or "").lower() != digest.lower():
            return {"started": False, "message": "请先下载并校验新版本，再执行替换。"}
        return installer.apply(self.settings.home, install, release["latest"], release["current"],
                               Path(str(staged["path"])), digest)

    def overview(self, period: str = "24h") -> dict:
        if period not in {"24h", "7d", "30d"}:
            raise ValueError("时间范围须为 24h、7d 或 30d")
        if not self.status()["ready"]:
            return {"period": period, "activity": {"since": None, "days": []}, "metrics": [
                {"key": key, "label": label, "unit": unit, "current": 0, "points": []}
                for key, label, unit in (("sessions", "会话总结", "条"), ("memories", "知识与技能", "条"),
                                         ("estimated_tokens", "正文估算 token", "tokens"))
            ]}
        return self._vault().overview(period)

    def _model(self) -> ModelClient:
        settings = self.settings.load()
        if not settings.model.strip():
            raise ValueError("请先设置模型名称；基础保存和搜索不需要模型")
        return ModelClient(settings.base_url, settings.model, self.settings.api_key())

    def test_connection(self) -> dict:
        return self._model().test_connection()

    @staticmethod
    def _context_size(item):
        text = json.dumps(item, ensure_ascii=False)
        ascii_count = sum(c.isascii() for c in text)
        return (ascii_count + 3) // 4 + len(text) - ascii_count

    def search(self, query: str, project: str = "", limit: int = 5, target: str = "", method: str = "",
               environment: dict | None = None, mode: str = "auto") -> dict:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("请输入 1 到 500 字符的检索词")
        if mode not in {"auto", "text", "code"}:
            raise ValueError("检索模式须为 auto、text 或 code")
        vault = self._vault()
        errors = ErrorReports(vault).search(query, project, target, method, environment)
        # Omit a whole reminder if its applicability conditions do not fit the context budget.
        reminders, used = [], 0
        for item in errors:
            size = self._context_size(item)
            if used + size <= 300 and len(reminders) < 3:
                reminders.append(item)
                used += size
        results = vault.search(query, project, max(1, min(5, limit)), mode=mode)
        return {"vault_path": str(vault.path), "results": results, "errors": reminders, "mode": detect_mode(query, mode),
                "guidance": "匹配结果需核对全部条件；未知条件不代表匹配。conflict=true 时，仅当前任务需要选择才询问用户。历史成功不等于永久可靠。"}

    def bootstrap(self, cwd: str, session_id: str, agent: str, workspace_root: str = "", task_id: str = "", task: str = "") -> dict:
        if not session_id.strip() or not agent.strip():
            raise ValueError("需要宿主的稳定 session_id 和 Agent 名称")
        if not isinstance(task, str) or len(task) > 500:
            raise ValueError("当前任务须为不超过 500 字符的文本")
        vault = self._vault()
        key = "session:" + fingerprint(session_id)
        session = vault.state(key)
        workspace = session["workspace"] if session else vault.workspace(cwd, workspace_root)
        project = vault.match_project(workspace)
        if session and session["project"] != project:
            session["saved"] = False
        session = vault.state(key, {"saved": False, **(session or {}), "workspace": workspace,
                                   "project": project, "agent": agent})
        info = vault.project(session["project"]) if session["project"] else None
        result = {"workspace": workspace, "project": session["project"], "independent": not session["project"],
                  "project_directory": str(vault.root / Path(info["path"]).parent) if info else "",
                  "project_context": None, "task": None, "relevant": [],
                  "guidance": "全局提示词全文遵循。所有会话保存完整总结；项目会话另存进度，独立会话不创建项目、任务或决策。安装卸载、工具配置、skills、模型和电脑环境变更由所有会话通过 changes 保存对象、实际位置、当前状态及验证；普通源码修改留在项目。真实失败用 errors；无事项不建记录。search 默认空 project 查全部，项目 ID 查本项目，independent 查公共池，all 查全部。草稿只存放，由用户手动提供，不搜索、不注入、不维护。子代理仅回传。"}
        if info:
            result["project_context"] = {k: info.get(k) for k in ("path", "title", "status", "goal", "next_actions")}
            parent_id = info.get("parent_project_id", "")
            if parent_id:
                parent = vault.project(parent_id)
                result["project_context"]["parent"] = {"project_id": parent_id, "title": parent["title"]}
                result["guidance"] += " 子项目检索会实时继承主项目知识；保存仍归当前子项目。成果提交与合并由用户在桌面选择执行。"
            result["project_context"]["progress"] = info.get("progress", "")[:1600]
            result["project_context"]["notes"] = info["body"][:1600]
            if task_id:
                path = vault.root / Path(info["path"]).parent / "任务" / (task_id + ".md")
                if not path.resolve().is_relative_to(vault.root):
                    raise ValueError("无效任务路径")
                if path.exists():
                    meta, body = vault.read(path)
                    result["task"] = {"path": str(path), "goal": meta.get("goal"), "progress": body[:1600], "next_actions": meta.get("next_actions", [])}
            candidates = ErrorReports(vault).search(info.get("goal", "") + " " + info["body"][:400], session["project"], high_only=True)
            result["pitfalls"] = []
            used = 0
            for item in candidates:
                size = self._context_size(item)
                if used + size <= 120 and len(result["pitfalls"]) < 2:
                    result["pitfalls"].append(item)
                    used += size
        prompt = vault.global_prompt()
        if prompt.strip():
            result["global_prompt"] = prompt
        if task.strip():
            result["relevant"] = vault.search(task, project, 3, kinds=("memory", "skill", "object"))
        vault.state("connection:" + fingerprint(agent.casefold())[:20], {"agent": agent, "last_seen": now()})
        return result

    def _session(self, vault, context):
        if not isinstance(context, dict) or context.get("role") != "root" or not context.get("session_id") or context.get("root_session_id") != context["session_id"]:
            raise ValueError("共享保存仅限宿主标识的根代理；子代理向根代理回传")
        key = "session:" + fingerprint(context["session_id"])
        session = vault.state(key)
        if not session:
            raise ValueError("请先 bootstrap 获取工作区归属")
        session["project"] = vault.match_project(session["workspace"])
        return key, session

    def save(self, context: dict, summary: str = "", goal: str = "", progress: str = "", next_actions: list[str] | None = None,
             task_id: str = "", memory: dict | None = None, errors: list[dict] | None = None, errors_reviewed: bool = False,
             project_status: str = "", decision: dict | None = None, changes: list[dict] | None = None) -> dict:
        vault = self._vault()
        key, session = self._session(vault, context)
        if any(not isinstance(v, str) or len(v) > 12000 for v in (summary, goal, progress)):
            raise ValueError("总结、目标和进度须为不超过 12,000 字符的文本")
        actions = next_actions or []
        if not isinstance(actions, list) or len(actions) > 20 or any(not isinstance(v, str) or len(v) > 1000 for v in actions):
            raise ValueError("下一步最多 20 条，每条不超过 1000 字符")
        if project_status and project_status not in {"active", "paused", "completed"}:
            raise ValueError("项目状态须为 active、paused 或 completed")
        if task_id and not session["project"]:
            raise ValueError("独立会话不创建任务卡")
        if memory and not session["project"]:
            raise ValueError("独立会话通过 changes 保存实际电脑与工具变更，不保存普通会话经验")
        if changes is not None:
            if not isinstance(changes, list):
                raise ValueError("changes 须为对象变更列表")
            for change in changes:
                if not isinstance(change, dict) or set(change) - {"object", "action", "location", "state", "evidence", "conditions"}:
                    raise ValueError("对象变更字段不正确")
                if any(not isinstance(change.get(k), str) or not change[k].strip() for k in ("object", "action", "location", "state")):
                    raise ValueError("对象变更需要 object、action、location、state 文本")
                if not isinstance(change.get("evidence", ""), str) or not isinstance(change.get("conditions", {}), dict):
                    raise ValueError("验证依据须为文本，适用条件须为键值表")
                if any(not isinstance(k, str) or not isinstance(v, str) for k, v in change.get("conditions", {}).items()):
                    raise ValueError("适用条件须为文本键值表")
        project = session["project"]
        result = {"message": "阶段已保存", "project": project}
        if summary.strip():
            result["summary"] = vault.save_session(context["session_id"], session["agent"], project, summary, goal)
        if project and any((goal, progress, next_actions is not None, project_status)):
            info = vault.project(project)
            metadata, old_body = vault.read(vault.root / info["path"])
            metadata.update(goal=goal or metadata.get("goal", ""), next_actions=actions if next_actions is not None else metadata.get("next_actions", []))
            if project_status:
                metadata["status"] = project_status
            if progress:
                metadata["progress"] = progress
            result["project_note"] = vault.write(info["path"], metadata, old_body)
        if task_id:
            result["task"] = vault.save_task(task_id, project, goal, progress, actions)
        if decision:
            if not project or not isinstance(decision.get("title"), str) or not isinstance(decision.get("body"), str):
                raise ValueError("决策需已关联项目及 title/body")
            info = vault.project(project)
            path = Path(info["path"]).parent / "决策" / (fingerprint(decision["title"])[:20] + ".md")
            result["decision"] = vault.write(path.as_posix(), {"kind": "decision", "title": decision["title"], "project": project}, decision["body"])
        if errors:
            result["error_report"] = ErrorReports(vault).save(context["session_id"], session["agent"], project, errors)
        if memory:
            if set(memory) - {"title", "body", "verified", "evidence", "conditions"}:
                raise ValueError("经验字段不正确")
            result["memory"] = vault.save_memory(project=project, sources=[result["summary"]["path"]] if "summary" in result else [], **memory)
        if changes:
            result["changes"] = [vault.save_change(change, session["agent"], context["session_id"]) for change in changes]
        session["saved"] = errors_reviewed and bool(summary.strip())
        vault.state(key, session)
        vault.refresh()
        return result

    def feedback(self, context: dict, error_session_id: str, error_id: str, observation: dict):
        vault = self._vault()
        self._session(vault, context)
        return ErrorReports(vault).feedback(error_session_id, error_id, observation)

    def hook(self, event: dict):
        # The host adapter supplies root identity and a stable user-turn ID, never transcript contents.
        if event.get("role") != "root" or event.get("root_session_id") != event.get("session_id"):
            return {}
        kind = event.get("event")
        if kind == "start":
            return {"additionalContext": "调用 Shared Brain bootstrap，传入宿主 session_id、agent、cwd/workspace_root；任务明确时带 task。全局提示词非空则全文加载；项目由应用配置，未匹配直接独立。独立会话也保存完整总结；实际电脑与工具变更用 changes 保存，真实失败用 errors。草稿仅用户手动提供。"}
        vault = self._vault()
        key = "session:" + fingerprint(event.get("session_id", ""))
        session = vault.state(key)
        if not session:
            reminder_key = "unbound-hook:" + fingerprint(event.get("session_id", ""))
            if kind == "turn":
                vault.state(reminder_key, {"reminded": False})
            elif kind == "closeout" and not (vault.state(reminder_key) or {}).get("reminded"):
                vault.state(reminder_key, {"reminded": True})
                return {"decision": "block", "reason": "请先 bootstrap 加载全局提示词并获取归属。项目和独立会话均保存完整总结；实际变更和错误按需记录。本轮只提醒一次。"}
            return {}
        session["project"] = vault.match_project(session["workspace"])
        if kind == "turn":
            session.update(saved=False, reminded=False)
            vault.state(key, session)
        elif kind == "failure" and event.get("actual_failure") is True and not event.get("expected_probe"):
            item = event.get("error", {})
            session["project"] = vault.match_project(session["workspace"])
            ErrorReports(vault).save(event["session_id"], session["agent"], session["project"], [item])
            session["saved"] = False
            vault.state(key, session)
        elif kind == "closeout":
            turn_id = event.get("turn_id", "")
            if turn_id and session.get("turn_id") != turn_id:
                session.update(turn_id=turn_id, reminded=False)
            if not session.get("saved") and not session.get("reminded"):
                session["reminded"] = True
                vault.state(key, session)
                instruction = "保存完整总结及项目进度" if session["project"] else "保存完整会话总结，并用 changes 补充实际电脑与工具变更"
                return {"decision": "block", "reason": f"请根代理检查实际失败并补全错误记录，{instruction}，再 save(errors_reviewed=true)。无错误不建空报告。本轮只提醒一次。"}
        return {}

    def import_document(self, path: str) -> dict:
        vault = self._vault()
        saved = vault.import_document(Path(path).expanduser())
        vault.refresh()
        return {"message": "文档副本已导入，原文件保持不变" if not saved.get("unchanged") else "相同内容已经导入", **saved}

    def maintain(self) -> dict:
        return maintain(self)

    def mcp_config(self) -> dict:
        args = [] if getattr(sys, "frozen", False) else ["-m", "shared_brain"]
        args += ["--home", str(self.settings.home.resolve()), "mcp"]
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        return {"mcpServers": {"shared_brain": {"command": str(executable), "args": args}}}
