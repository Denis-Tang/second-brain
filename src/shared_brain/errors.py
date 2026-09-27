"""One bounded, structured error report per root conversation; no model calls."""
import json
import re
from urllib.parse import urlsplit, urlunsplit
from .vault import fingerprint, now


def scrub(value):
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def clean(text):
    text = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[redacted]", text)
    return re.sub(r"(?i)((?:api[_-]?key|token|password|cookie|secret)\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", text)


def target(value):
    if value.startswith(("https://", "http://")):
        url = urlsplit(value)
        # URL query strings often contain credentials. Agents describe relevant nonsecret parameters in environment.
        return urlunsplit((url.scheme.lower(), url.netloc.split("@")[-1].lower(), url.path or "/", "", ""))
    return clean(value)


class ErrorReports:
    def __init__(self, vault):
        self.vault = vault
        self.root = vault.reports

    def path(self, session_id):
        return self.root / (fingerprint(session_id) + ".json")

    def read(self, session_id):
        path = self.path(session_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"session_id": session_id, "errors": []}

    def save(self, session_id, agent, project, errors):
        if not isinstance(errors, list) or len(errors) > 50:
            raise ValueError("每次最多提交 50 个错误条目")
        report = self.read(session_id)
        entries = {e["id"]: e for e in report["errors"]}
        for item in errors:
            if not isinstance(item, dict) or set(item) - {"id", "target", "method", "environment", "symptom", "attempts", "workaround", "impact", "global_scope"}:
                raise ValueError("错误条目字段不正确")
            for field in ("target", "method", "symptom"):
                if not isinstance(item.get(field), str) or not 1 <= len(item[field]) <= 1000:
                    raise ValueError("错误报告需要不超过 1000 字符的 target、method、symptom")
            env = item.get("environment", {})
            if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
                raise ValueError("environment 必须为文本键值表")
            if len(json.dumps(item, ensure_ascii=False)) > 6000:
                raise ValueError("错误条目过长，请保留必要现象与尝试")
            if item.get("impact", "low") not in {"low", "high"}:
                raise ValueError("impact 只能是 low 或 high")
            for field in ("attempts", "workaround"):
                if not isinstance(item.get(field, ""), str):
                    raise ValueError("attempts 和 workaround 应为简短文本")
            item = scrub(item)
            item["target"] = target(item["target"])
            identity = item.get("id") or fingerprint(json.dumps([item["target"], item["method"], env], sort_keys=True))[:20]
            if not isinstance(identity, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
                raise ValueError("错误 ID 只能使用字母数字横线和下划线")
            previous = entries.get(identity, {})
            entries[identity] = {**previous, **item, "id": identity, "environment": item.get("environment", {}),
                                 "project": "" if item.get("global_scope") is True else project,
                                 "first_seen": previous.get("first_seen", now()), "last_seen": now(), "active": True}
        if not entries:
            return None
        report.update(agent=agent, project=project, updated=now(), errors=list(entries.values()))
        self.root.mkdir(parents=True, exist_ok=True)
        self.path(session_id).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"path": str(self.path(session_id)), "errors": len(entries)}

    def feedback(self, session_id, error_id, observation):
        report = self.read(session_id)
        entry = next((e for e in report["errors"] if e["id"] == error_id), None)
        if entry is None:
            raise ValueError("原错误条目不存在")
        if not isinstance(observation, dict) or not all(isinstance(observation.get(k), str) and observation[k].strip() for k in ("target", "method", "evidence")) or not isinstance(observation.get("environment"), dict):
            raise ValueError("成功反馈需要 target、method、environment 与实际结果 evidence")
        if len(json.dumps(observation, ensure_ascii=False)) > 4000:
            raise ValueError("成功反馈过长")
        observation = scrub(observation)
        observation["target"] = target(observation["target"])
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in observation["environment"].items()):
            raise ValueError("成功反馈环境须为文本键值表")
        comparable = observation["target"] == entry["target"] and observation["method"] == entry["method"] and observation["environment"] == entry["environment"] and bool(entry["environment"])
        if comparable:
            entry["active"] = False
        elif observation["target"] == entry["target"] and observation["method"] != entry["method"]:
            entry["workaround"] = observation["method"] + ": " + observation["evidence"]
        entry.setdefault("successes", []).append({**observation, "at": now(), "comparable": comparable})
        report["updated"] = now()
        self.path(session_id).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.vault.refresh()
        with self.vault.connect() as con:
            notes = list(con.execute("SELECT path,metadata FROM notes WHERE kind IN ('memory','skill')"))
        for row in notes:
            metadata = json.loads(row["metadata"])
            if str(self.path(session_id)) in metadata.get("sources", []):
                _, body = self.vault.read(self.vault.root / row["path"])
                self.vault.write(row["path"], {**metadata, "feedback_pending": True}, body)
        return {"active": entry["active"], "path": str(self.path(session_id)), "comparable": comparable}

    def search(self, query, project="", object_target="", method="", environment=None, high_only=False):
        terms = re.findall(r"\w+", query.casefold())
        results = []
        for path in self.root.glob("*.json"):
            report = json.loads(path.read_text(encoding="utf-8"))
            for e in report["errors"]:
                if e["project"] not in {"", project} or (high_only and (e.get("impact") != "high" or not e["active"])):
                    continue
                if object_target and target(object_target) != e["target"] or method and method != e["method"]:
                    continue
                conditions = e["environment"]
                if environment and any(conditions.get(k) != v for k, v in environment.items()):
                    continue
                text = json.dumps(e, ensure_ascii=False)
                score = sum(t in text.casefold() for t in terms)
                if not score and not object_target:
                    continue
                results.append({"path": str(path), "session_id": report["session_id"], "error_id": e["id"],
                                "kind": "error", "target": e["target"], "method": e["method"], "environment": conditions,
                                "active": e["active"], "at": e["last_seen"], "text": e["symptom"],
                                "workaround": e.get("workaround", ""), "latest_success": (e.get("successes") or [None])[-1], "score": score})
        return sorted(results, key=lambda r: (r["score"], r["at"]), reverse=True)

    def pending(self):
        result = []
        for path in self.root.glob("*.json"):
            body = path.read_text(encoding="utf-8")
            report = json.loads(body)
            digest = fingerprint(body)
            if self.vault.state("processed-error:" + path.name) != digest:
                result.append({"path": str(path), "hash": digest, "project": report["project"], "kind": "error",
                               "title": "错误报告", "body": body})
        return result
