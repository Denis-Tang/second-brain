"""Incremental maintenance with one application-wide durable cost ledger."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from pathlib import Path
from .errors import ErrorReports
from .model import EXTRACTION_PROMPT
from .vault import now

MONTHLY_CNY = 10.0
NIGHT_INPUT = 30_000
NIGHT_OUTPUT = 4_000
NIGHT_CALLS = 3


def billing_time():
    return datetime.now(timezone(timedelta(hours=8)))


def ledger(home):
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(home / "maintenance.sqlite3")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, day TEXT, month TEXT, input INTEGER, output INTEGER, cny REAL, settled INTEGER)")
    return con


def totals(con, at):
    day = con.execute("SELECT count(*),coalesce(sum(input),0),coalesce(sum(output),0) FROM usage WHERE day=?", (at.strftime("%Y-%m-%d"),)).fetchone()
    month = con.execute("SELECT coalesce(sum(cny),0) FROM usage WHERE month=?", (at.strftime("%Y-%m"),)).fetchone()[0]
    return day, month


def usage_status(home):
    with ledger(home) as con:
        day, month = totals(con, billing_time())
        reserved = con.execute("SELECT count(*) FROM usage WHERE settled=0").fetchone()[0]
    con.close()
    return {"monthly_limit_cny": MONTHLY_CNY, "used_or_reserved_cny": round(month, 6),
            "remaining_cny": round(max(0, MONTHLY_CNY - month), 6), "today_calls": day[0],
            "today_input": day[1], "today_output": day[2], "unsettled_requests": reserved,
            "night_input_limit": NIGHT_INPUT, "night_output_limit": NIGHT_OUTPUT, "night_call_limit": NIGHT_CALLS}


def token_bound(sources, existing):
    # UTF-8 bytes conservatively bound tokenizer input, including prompt and message overhead.
    return len((EXTRACTION_PROMPT + json.dumps({"sources": sources, "existing": existing}, ensure_ascii=False)).encode("utf-8")) + 256


def reserve(home, inputs, outputs):
    at = billing_time()
    cost = (inputs * 2 + outputs * 8) / 1_000_000  # reserve at peak prices, regardless of scheduled hour
    with ledger(home) as con:
        con.execute("BEGIN IMMEDIATE")
        day, month = totals(con, at)
        if day[0] >= NIGHT_CALLS or day[1] + inputs > NIGHT_INPUT or day[2] + outputs > NIGHT_OUTPUT or month + cost > MONTHLY_CNY:
            result = None
        else:
            result = con.execute("INSERT INTO usage(day,month,input,output,cny,settled) VALUES (?,?,?,?,?,0)",
                                 (at.strftime("%Y-%m-%d"), at.strftime("%Y-%m"), inputs, outputs, cost)).lastrowid
    con.close()
    return result


def settle(home, request_id, usage, started):
    if not isinstance(usage, dict):
        return  # unknown billed result: retain maximum reservation, never assume free
    inputs, outputs = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if not isinstance(inputs, int) or not isinstance(outputs, int) or min(inputs, outputs) < 0:
        return
    hits = usage.get("prompt_cache_hit_tokens", 0)
    if not isinstance(hits, int) or not 0 <= hits <= inputs:
        hits = 0
    def peak(at):
        return at.weekday() < 5 and (9 <= at.hour < 12 or 14 <= at.hour < 18)
    factor = 2 if peak(started) or peak(billing_time()) else 1
    cost = ((inputs - hits) + hits * .02 + outputs * 4) * factor / 1_000_000
    with ledger(home) as con:
        con.execute("UPDATE usage SET input=?,output=?,cny=?,settled=1 WHERE id=?", (inputs, outputs, cost, request_id))
    con.close()


def maintain(service):
    vault = service._vault()
    reports = ErrorReports(vault)
    receipt = {"processed": 0, "memories": 0, "model_calls": 0, "message": "没有新增资料，无需调用模型"}
    for _ in range(NIGHT_CALLS):
        error_pending = reports.pending()
        error_pending.sort(key=lambda row: any(e.get("impact") == "high" or e.get("attempts") or e.get("successes") for e in json.loads(row["body"])["errors"]), reverse=True)
        pending = error_pending + vault.pending()
        if not pending:
            break
        settings = service.settings.load()
        if settings.model != "deepseek-flash" or urlsplit(settings.base_url).hostname != "api.deepseek.com":
            raise ValueError("自动维护费用核算目前支持官方 deepseek-flash；请使用默认模型和官方 API 地址")
        budget = usage_status(service.settings.home)
        available = NIGHT_INPUT - budget["today_input"]
        output = min(1400, NIGHT_OUTPUT - budget["today_output"])
        if budget["today_calls"] >= NIGHT_CALLS or output < 200:
            receipt["message"] = "本日维护额度已用完，积压保留至下次"
            break
        project = pending[0]["project"]
        pending = [row for row in pending if row["project"] == project]
        existing = []
        query = pending[0]["title"] + " " + pending[0]["body"][:300]
        if pending[0]["kind"] == "error":
            entry = json.loads(pending[0]["body"])["errors"][0]
            query = entry["target"] + " " + entry["method"] + " " + entry["symptom"]
        for row in vault.search(query, project, 3):
            if row["kind"] in {"memory", "skill"}:
                metadata, body = vault.read(vault.path / row["path"])
                item = {"id": row["path"], "title": row["title"], "kind": row["kind"], "body": body, "conditions": metadata.get("conditions", {})}
                if token_bound([], existing + [item]) < min(6000, available // 2):
                    existing.append(item)
        batch, sources, offsets = [], [], {}
        seen = set()
        duplicates = []
        for row in pending:
            if row["hash"] in seen:
                duplicates.append(row)
                continue
            body = row["body"]
            offset_key = "offset:" + row["path"]
            previous = vault.state(offset_key) or {}
            offset = previous.get("offset", 0) if previous.get("hash") == row["hash"] else 0
            # Slice large inputs into bounded incremental pieces; never drop their unprocessed remainder.
            chunk = body[offset:offset + 1600]
            item = {"id": row["path"], "title": row["title"], "content": chunk, "partial": offset > 0 or len(body) > len(chunk)}
            if row["kind"] == "error":
                report = json.loads(body)
                entries = report["errors"]
                offset = previous.get("offset", 0) if previous.get("hash") == row["hash"] else 0
                if offset >= len(entries):
                    offset = 0
                item["content"] = json.dumps(entries[offset], ensure_ascii=False)
                end, complete = offset + 1, offset + 1 >= len(entries)
            else:
                end, complete = offset + len(chunk), offset + len(chunk) >= len(body)
            if token_bound(sources + [item], existing) > available:
                continue
            sources.append(item)
            batch.append(row)
            seen.add(row["hash"])
            offsets[row["path"]] = (end, complete)
            if len(batch) >= 4:
                break
        if not batch:
            receipt["message"] = "剩余额度无法容纳待处理条目，保留至下次"
            break
        reservation = reserve(service.settings.home, token_bound(sources, existing), output)
        if reservation is None:
            receipt["message"] = "预算或本日额度不足，积压已保留"
            break
        client = service._model()
        started = billing_time()
        try:
            notes = client.extract(sources, existing, max_tokens=output)
        finally:
            settle(service.settings.home, reservation, client.usage, started)
        written = []
        for note in notes:
            saved = vault.save_memory(note["title"], note["body"], project=project, sources=note["source_ids"],
                                      generated=True, kind=note["kind"], conflict=note["conflict"], conditions=note["conditions"])
            written.append(saved)
            for merged_path in note.get("merge_ids", []):
                if merged_path != saved["path"]:
                    metadata, body = vault.read(vault.root / merged_path)
                    if metadata.get("kind") in {"memory", "skill"}:
                        vault.write(merged_path, {**metadata, "merged_into": saved["path"]}, body)
                        written.append({"path": merged_path})
        receipt.update(processed=receipt["processed"] + len(batch), memories=receipt["memories"] + len(written),
                       model_calls=receipt["model_calls"] + 1, finished_at=now(), message="新增材料已增量整理；未处理部分留待下次")
        finished = []
        for row in batch:
            end, complete = offsets[row["path"]]
            vault.state("offset:" + row["path"], {"hash": row["hash"], "offset": end})
            if complete:
                if row["kind"] == "error":
                    vault.state("processed-error:" + Path(row["path"]).name, row["hash"])
                else:
                    finished.append(row)
        completed_hashes = {row["hash"] for row in batch if offsets[row["path"]][1]}
        for row in duplicates:
            if row["hash"] in completed_hashes:
                if row["kind"] == "error":
                    vault.state("processed-error:" + Path(row["path"]).name, row["hash"])
                else:
                    finished.append(row)
        vault.refresh()
        # Generated outputs are already processed; do not feed them back as fresh model inputs.
        with vault.connect() as con:
            for item in written:
                row = con.execute("SELECT path,hash FROM notes WHERE path=?", (item["path"],)).fetchone()
                if row:
                    finished.append(dict(row))
        vault.mark_processed(finished, receipt)
    receipt["budget"] = usage_status(service.settings.home)
    return receipt
