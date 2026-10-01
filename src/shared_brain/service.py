"""One application core shared by the desktop interface and MCP."""

import json
import sys
from dataclasses import asdict
from pathlib import Path

from .model import ModelClient
from .onboarding import build_prompt
from .settings import SettingsStore
from .vault import Vault, now, fingerprint
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
        result = {"settings": values, "key_configured": configured, "ready": False, "global_prompt": "",
                  "counts": {"tasks": 0, "sessions": 0, "memories": 0, "sources": 0}, "pending": 0, "last_maintenance": None}
        if settings.vault_path and all((Path(settings.vault_path) / folder).is_dir()
                                       for folder in ("项目", "会话总结", "知识", "技能")):
            vault = self._vault()
            result["global_prompt"] = vault.global_prompt()
            result["ready"] = True
            result["pending"] = len(vault.pending()) + len(ErrorReports(vault).pending())
            with vault.connect() as con:
                counts = {r[0]: r[1] for r in con.execute("SELECT kind,count(*) FROM notes GROUP BY kind")}
                result["counts"] = {"tasks": counts.get("task", 0), "memories": counts.get("memory", 0) + counts.get("skill", 0) + counts.get("object", 0),
                                    "sources": counts.get("source", 0), "sessions": counts.get("session", 0)}
                row = con.execute("SELECT value FROM state WHERE key='last_maintenance'").fetchone()
                result["last_maintenance"] = json.loads(row[0]) if row else None
        result["budget"] = usage_status(self.settings.home)
        return result

    def initialize(self, vault_path: str) -> dict:
        settings = self.settings.update({"vault_path": vault_path})
        self._vault()
        path = settings.vault_path
        return {"message": "知识库已就绪，可配置项目并复制提示词接入 Agent", "path": str(path)}

    def configure(self, values: dict, api_key: str | None = None) -> dict:
        self.settings.update(values, api_key)
        if self.settings.load().vault_path:
            self._vault()
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

    def configure_project(self, name: str, paths: list[str], project_id: str = "") -> dict:
        return {"project": self._vault().configure_project(name, paths, project_id), "message": "项目配置已保存"}

    def delete_project(self, project_id: str) -> dict:
        self._vault().delete_project(project_id)
        return {"message": "项目立项已删除，历史资料已保留"}

    def agent_prompt(self) -> dict:
        path = self.settings.load().vault_path
        if not path:
            raise ValueError("请先选择并保存仓库路径")
        return {"text": build_prompt(Path(path), self.mcp_config())}

    def overview(self, period: str = "24h") -> dict:
        if period not in {"24h", "7d", "30d"}:
            raise ValueError("时间范围须为 24h、7d 或 30d")
        if not self.status()["ready"]:
            return {"period": period, "metrics": [
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

    def search(self, query: str, project: str = "", limit: int = 5, target: str = "", method: str = "", environment: dict | None = None) -> dict:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("请输入 1 到 500 字符的检索词")
        vault = self._vault()
        errors = ErrorReports(vault).search(query, project, target, method, environment)
        # Omit a whole reminder if its applicability conditions do not fit the context budget.
        reminders, used = [], 0
        for item in errors:
            size = self._context_size(item)
            if used + size <= 300 and len(reminders) < 3:
                reminders.append(item)
                used += size
        results = vault.search(query, project, max(1, min(5, limit)))
        return {"vault_path": str(vault.path), "results": results, "errors": reminders,
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
            session["saved"] = not project
        session = vault.state(key, {"saved": not project, **(session or {}), "workspace": workspace,
                                   "project": project, "agent": agent})
        info = vault.project(session["project"]) if session["project"] else None
        result = {"workspace": workspace, "project": session["project"], "independent": not session["project"],
                  "project_directory": str(vault.root / Path(info["path"]).parent) if info else "",
                  "project_context": None, "task": None, "relevant": [],
                  "guidance": "全局提示词全文遵循。项目会话保存完整总结及进度；独立会话不写总结、任务或决策。安装卸载、工具配置、skills、模型和电脑环境变更由所有会话通过 changes 保存对象、实际位置、当前状态及验证；普通源码修改留在项目。真实失败用 errors；无事项不建记录。search 默认空 project 查全部，项目 ID 查本项目，independent 查公共池，all 查全部。草稿只存放，由用户手动提供，不搜索、不注入、不维护。子代理仅回传。"}
        if info:
            result["project_context"] = {k: info.get(k) for k in ("path", "title", "status", "goal", "next_actions")}
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
        if project and summary.strip():
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
        session["saved"] = errors_reviewed and (not project or bool(summary.strip()))
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
            return {"additionalContext": "调用 Shared Brain bootstrap，传入宿主 session_id、agent、cwd/workspace_root；任务明确时带 task。全局提示词非空则全文加载；项目由应用配置，未匹配直接独立。独立会话不写总结；实际电脑与工具变更用 changes 保存，真实失败用 errors。草稿仅用户手动提供。"}
        vault = self._vault()
        key = "session:" + fingerprint(event.get("session_id", ""))
        session = vault.state(key)
        if not session:
            reminder_key = "unbound-hook:" + fingerprint(event.get("session_id", ""))
            if kind == "turn":
                vault.state(reminder_key, {"reminded": False})
            elif kind == "closeout" and not (vault.state(reminder_key) or {}).get("reminded"):
                vault.state(reminder_key, {"reminded": True})
                return {"decision": "block", "reason": "请先 bootstrap 加载全局提示词并获取归属。项目保存总结；独立会话仅保存实际变更和错误，无事项不建记录。本轮只提醒一次。"}
            return {}
        session["project"] = vault.match_project(session["workspace"])
        if kind == "turn":
            session.update(saved=not session["project"], reminded=False)
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
                instruction = "保存完整总结及项目进度" if session["project"] else "核对实际电脑与工具变更，用 changes 补充对象记录；不写会话总结"
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
