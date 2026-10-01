"""Compare local search in memory; callers supply notes and labelled queries."""

import json
import math
import random
import re
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shared_brain.vault import search_terms

METHODS = ("baseline", "scan_sql", "bigram_count", "bigram_bm25")
CJK = r"[\u3400-\u4dbf\u4e00-\u9fff]"
VISIBLE = " AND NOT coalesce(json_extract(n.metadata,'$.feedback_pending'),0) AND coalesce(json_extract(n.metadata,'$.merged_into'),'')=''"


def document_tokens(text):
    # Keep every occurrence and the entire document for BM25 term frequencies.
    return " ".join(
        part[i:i + 2] if re.match(CJK, part) else part
        for token in re.findall(r"\w+", text.casefold())
        for part in re.findall(CJK + r"+|[^\u3400-\u4dbf\u4e00-\u9fff]+", token)
        for i in range(max(1, len(part) - 1) if re.match(CJK, part) else 1)
    )


def prepare(notes):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE notes (path TEXT PRIMARY KEY, title TEXT, kind TEXT,
            project TEXT, verified INTEGER, body TEXT, hash TEXT, metadata TEXT);
        CREATE VIRTUAL TABLE search USING fts5(path UNINDEXED,text,tokenize='trigram');
        CREATE VIRTUAL TABLE words USING fts5(path UNINDEXED,title,body,tokenize='unicode61');
    """)
    for note in notes:
        meta = note.get("metadata", {})
        if isinstance(meta, str):
            meta = json.loads(meta)
        title, body = note["title"], note["body"]
        con.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?)", (
            note["path"], title, note["kind"], note.get("project", ""),
            int(note.get("verified", False)), body, note.get("hash", ""),
            json.dumps(meta, ensure_ascii=False),
        ))
        con.execute("INSERT INTO search VALUES (?,?)", (note["path"], meta.get("title", "") + "\n" + body))
        con.execute("INSERT INTO words VALUES (?,?,?)", (note["path"], document_tokens(title), document_tokens(body)))
    con.commit()
    return con


def search(con, method, query, project="", kinds=None, limit=5, details=False):
    terms = search_terms(query)
    if not terms:
        return []
    chinese = bool(re.search(CJK, query))
    minimum = 2 if chinese and len(terms) >= 3 else 1
    scope = "" if project in {"", "all"} else " AND n.project=?"
    params = [] if not scope else ["" if project == "independent" else project]
    if kinds:
        scope += " AND n.kind IN (" + ",".join("?" for _ in kinds) + ")"
        params += list(kinds)
    expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    visible = "" if method == "baseline" else VISIBLE
    if not chinese and all(len(t) >= 3 for t in terms):
        sql = "SELECT n.* FROM search JOIN notes n ON n.path=search.path WHERE search MATCH ?" + scope + visible
        sql += " ORDER BY rank,n.verified DESC,(n.kind='memory') DESC"
        arguments = [expression, *params]
    else:
        score = " + ".join("(instr(lower(title || ' ' || body), lower(?))>0)" for _ in terms)
        # Mixed/short queries retain the existing substring semantics.
        indexed = method.startswith("bigram_") and all(re.fullmatch(CJK + "{2}", t) for t in terms)
        if indexed:
            score = score.replace("title || ' ' || body", "n.title || ' ' || n.body")
            sql = f"SELECT n.*, ({score}) AS score FROM words JOIN notes n ON n.path=words.path WHERE words MATCH ? AND score>=?"
            arguments = [*terms, expression, minimum, *params]
        else:
            sql = f"SELECT n.*, ({score}) AS score FROM notes n WHERE score>=?"
            arguments = [*terms, minimum, *params]
        sql += scope + visible
        if method != "baseline":
            ordering = "bm25(words,0,2,1)" if indexed and method == "bigram_bm25" else "score DESC"
            sql += " ORDER BY " + ordering + ",n.verified DESC,n.path"
    if method != "baseline":
        sql += " LIMIT ?"
        arguments.append(limit)
    rows = con.execute(sql, arguments).fetchall()
    if method == "baseline" and (chinese or not all(len(t) >= 3 for t in terms)):
        rows.sort(key=lambda row: (-row["score"], -row["verified"], row["path"]))
    result = []
    for row in rows:
        meta = json.loads(row["metadata"])
        if not meta.get("feedback_pending") and not meta.get("merged_into"):
            result.append((row, meta) if details else row["path"])
            if len(result) == limit:
                break
    return result


def full_search(vault, method, query, project="", limit=5, kinds=None):
    """Keep production refresh, connection and result formatting on a temporary home."""
    terms = search_terms(query)
    if not terms:
        return []
    vault.refresh()
    with vault.connect() as con:
        rows = search(con, method, query, project, kinds, limit, details=True)
    result = []
    for row, metadata in rows:
        body = row["body"]
        positions = [body.casefold().find(t.casefold()) for t in terms]
        start = max(0, min((p for p in positions if p >= 0), default=0) - 60)
        result.append({"path": row["path"], "title": row["title"][:160], "kind": row["kind"],
                       "project": row["project"], "conditions": metadata.get("conditions", {}),
                       "evidence": metadata.get("evidence", ""), "sources": metadata.get("sources", []),
                       "verified": bool(row["verified"]), "conflict": metadata.get("conflict", False), "text": body[start:start + 600]})
        if row["kind"] == "object":
            result[-1].update(location=metadata.get("location", ""), current_state=metadata.get("current_state", ""))
    return result


def quality(queries, rankings):
    hits, ndcgs, false_positives = [], [], []
    for query, found in zip(queries, rankings):
        if "relevance" not in query:
            continue
        relevance = query["relevance"]
        ideal = sorted(relevance.values(), reverse=True)[:5]
        if not any(ideal):
            false_positives.append(bool(found))
            continue
        hits.append(any(relevance.get(path, 0) > 0 for path in found))
        dcg = sum((2 ** relevance.get(path, 0) - 1) / math.log2(rank + 2) for rank, path in enumerate(found))
        idcg = sum((2 ** grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(ideal))
        ndcgs.append(dcg / idcg)
    return {"hit_at_5": statistics.mean(hits) if hits else None,
            "ndcg_at_5": statistics.mean(ndcgs) if ndcgs else None,
            "false_positive_rate": statistics.mean(false_positives) if false_positives else None,
            "positive_queries": len(hits), "no_answer_queries": len(false_positives)}


def run(notes, queries, repeats=30, warmup=5, seed=20261001):
    started = time.perf_counter()
    con = prepare(notes)
    build_ms = (time.perf_counter() - started) * 1000
    rankings = {method: [search(con, method, q["query"], q.get("project", ""), q.get("kinds")) for q in queries] for method in METHODS}
    for _ in range(warmup):
        for q in queries:
            for method in METHODS:
                search(con, method, q["query"], q.get("project", ""), q.get("kinds"))
    rng = random.Random(seed)
    samples = {method: [[] for _ in queries] for method in METHODS}
    tasks = [(i, method) for i in range(len(queries)) for method in METHODS]
    for _ in range(repeats):
        rng.shuffle(tasks)
        for i, method in tasks:
            q = queries[i]
            started = time.perf_counter_ns()
            search(con, method, q["query"], q.get("project", ""), q.get("kinds"))
            samples[method][i].append((time.perf_counter_ns() - started) / 1_000_000)
    metrics = {}
    print(f"Pure in-memory retrieval; notes={len(notes)}, queries={len(queries)}, repeats={repeats}, SQLite={sqlite3.sqlite_version}; combined index build={build_ms:.1f}ms")
    print("method            Hit@5  nDCG@5   no-answer FP   median ms    P95 ms")
    for method in METHODS:
        measured = sorted(value for query_samples in samples[method] for value in query_samples)
        metrics[method] = {**quality(queries, rankings[method]),
                           "median_ms": statistics.median(measured),
                           "p95_ms": measured[math.ceil(len(measured) * .95) - 1]}
        m = metrics[method]
        show = lambda value: "-" if value is None else f"{value:.3f}"
        print(f"{method:17} {show(m['hit_at_5']):>5}  {show(m['ndcg_at_5']):>6}  {show(m['false_positive_rate']):>13}  {m['median_ms']:10.3f}  {m['p95_ms']:8.3f}")
    con.close()
    return {"metrics": metrics, "rankings": rankings, "samples_ms": samples, "index_build_ms": build_ms}


def full_search_benchmark():
    """Include Markdown refresh and SQLite IO, with disposable synthetic notes."""
    from shared_brain.service import BrainService

    with tempfile.TemporaryDirectory(prefix="shared-brain-load-") as td:
        root = Path(td)
        service = BrainService(root / "home")
        service.initialize(str(root / "vault"))
        vault = service._vault()
        previous = 0
        for count in (100, 1000, 5000):
            for i in range(previous, count):
                vault.write(f"项目/独立项目/独立知识/load{i:04d}.md",
                            {"kind": "memory", "title": f"load{i:04d}", "project": "", "verified": False},
                            f"loadmarker{i:04d} 配置与验证步骤。" * 8)
            vault.refresh()
            samples, hits = [], 0
            for i in range(100):
                expected = f"loadmarker{(i * 47) % count:04d}"
                started = time.perf_counter()
                found = service.search(expected)["results"]
                samples.append((time.perf_counter() - started) * 1000)
                hits += any(expected in item["text"] for item in found)
            ordered = sorted(samples)
            print(f"FULL_SEARCH notes={count} hits={hits}/100 median_ms={statistics.median(samples):.1f} "
                  f"p95_ms={ordered[94]:.1f}", flush=True)
            previous = count


if __name__ == "__main__":
    full_search_benchmark()
