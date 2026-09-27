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
        if not all((vault.root / folder).is_dir() for folder in ("项目", "会话总结", "知识", "技能")):
            raise ValueError("请复制设置页的内置提示词，由 Agent 创建仓库目录并完成接入")
        return vault

    def status(self) -> dict:
        settings = self.settings.load()
        values = asdict(settings)
        configured = values.pop("key_configured")
        result = {"settings": values, "key_configured": configured, "ready": False,
                  "counts": {"tasks": 0, "sessions": 0, "memories": 0, "sources": 0}, "pending": 0, "last_maintenance": None}
        if settings.vault_path and all((Path(settings.vault_path) / folder).is_dir()
                                       for folder in ("项目", "会话总结", "知识", "技能")):
            vault = self._vault()
            result["ready"] = True
            result["pending"] = len(vault.pending()) + len(ErrorReports(vault).pending())
            with vault.connect() as con:
                counts = {r[0]: r[1] for r in con.execute("SELECT kind,count(*) FROM notes GROUP BY kind")}
                result["counts"] = {"tasks": counts.get("task", 0), "memories": counts.get("memory", 0),
                                    "sources": counts.get("source", 0), "sessions": counts.get("session", 0)}
                row = con.execute("SELECT value FROM state WHERE key='last_maintenance'").fetchone()
                result["last_maintenance"] = json.loads(row[0]) if row else None
        result["budget"] = usage_status(self.settings.home)
        return result

    def initialize(self, vault_path: str) -> dict:
        if not isinstance(vault_path, str) or not vault_path.strip():
            raise ValueError("请选择知识库目录")
        path = Path(vault_path).expanduser().resolve()
        if path.exists() and not path.is_dir():
            raise ValueError("请选择文件夹路径")
        self.settings.update({"vault_path": str(path)})
        return {"message": "仓库路径已保存，请复制内置提示词交给 Agent 完成配置", "path": str(path)}

    def configure(self, values: dict, api_key: str | None = None) -> dict:
        self.settings.update(values, api_key)
        return {"message": "设置已保存", **self.status()}

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
                for key, label, unit in (("sessions", "会话总结", "条"), ("memories", "知识（含草稿）", "条"),
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

    def bootstrap(self, cwd: str, session_id: str, agent: str, workspace_root: str = "", choice: str = "", project: str = "", project_name: str = "", task_id: str = "") -> dict:
        if not session_id.strip() or not agent.strip():
            raise ValueError("需要宿主的稳定 session_id 和 Agent 名称")
        vault = self._vault()
        key = "session:" + fingerprint(session_id)
        session = vault.state(key)
        workspace = session["workspace"] if session else vault.workspace(cwd, workspace_root)
        binding = vault.bind(workspace, choice, project, project_name) if choice else vault.state("workspace:" + workspace)
        if not binding:
            return {"needs_choice": True, "workspace": workspace,
                    "question": "这个路径是新工作区、关联已有项目，还是长期作为独立会话？",
                    "choices": ["new", "existing", "independent"],
                    "projects": [{"project_id": p["project_id"], "title": p["title"]} for p in vault.projects()]}
        if not session or choice:
            session = vault.state(key, {"workspace": workspace, "project": binding["project_id"], "agent": agent, "saved": False})
        info = vault.project(session["project"]) if session["project"] else None
        result = {"needs_choice": False, "workspace": workspace, "project": session["project"], "independent": not session["project"],
                  "project_context": None, "task": None,
                  "guidance": "按需 search；有用阶段由根代理 save 完整总结并更新项目进度。同一会话更新同一总结。实际失败单独记录；已验证经验可当场保存。子代理仅回传。冲突等当前任务需要选择时才问用户。"}
        if info:
            result["project_context"] = {k: info.get(k) for k in ("path", "title", "status", "goal", "next_actions")}
            result["project_context"]["progress"] = info["body"][:1600]
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
        preferences = vault.root / "偏好.md"
        if preferences.exists():
            result["preferences"] = preferences.read_text(encoding="utf-8-sig")[:1200]
        return result

    def _session(self, vault, context):
        if not isinstance(context, dict) or context.get("role") != "root" or not context.get("session_id") or context.get("root_session_id") != context["session_id"]:
            raise ValueError("共享保存仅限宿主标识的根代理；子代理向根代理回传")
        key = "session:" + fingerprint(context["session_id"])
        session = vault.state(key)
        if not session:
            raise ValueError("请先 bootstrap 并确认工作区归属")
        return key, session

    def save(self, context: dict, summary: str = "", goal: str = "", progress: str = "", next_actions: list[str] | None = None,
             task_id: str = "", memory: dict | None = None, errors: list[dict] | None = None, errors_reviewed: bool = False,
             project_status: str = "", decision: dict | None = None) -> dict:
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
            result["project_note"] = vault.write(info["path"], metadata, progress or old_body)
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
        if summary.strip() and errors_reviewed:
            session.update(saved=True)
        else:
            session["saved"] = False
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
            return {"additionalContext": "调用 Shared Brain bootstrap，传入宿主 session_id、agent、cwd/workspace_root；未知路径先询问归属。"}
        vault = self._vault()
        key = "session:" + fingerprint(event.get("session_id", ""))
        session = vault.state(key)
        if not session:
            reminder_key = "unbound-hook:" + fingerprint(event.get("session_id", ""))
            if kind == "turn":
                vault.state(reminder_key, {"reminded": False})
            elif kind == "closeout" and not (vault.state(reminder_key) or {}).get("reminded"):
                vault.state(reminder_key, {"reminded": True})
                return {"decision": "block", "reason": "请先 bootstrap 确认工作区归属，再检查错误并保存会话总结。本轮只提醒一次。"}
            return {}
        if kind == "turn":
            session.update(saved=False, reminded=False)
            vault.state(key, session)
        elif kind == "failure" and event.get("actual_failure") is True and not event.get("expected_probe"):
            item = event.get("error", {})
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
                return {"decision": "block", "reason": "请根代理检查实际失败并补全独立错误报告，再用 save 保存完整总结及项目进度，传 errors_reviewed=true；没有错误不生成空报告。本轮只提醒一次。"}
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
        return {"mcpServers": {"shared-brain": {"command": str(executable), "args": args}}}
