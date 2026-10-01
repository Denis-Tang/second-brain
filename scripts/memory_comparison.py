"""Real local product comparison; synthetic data only, stdout results, no model calls.

Install dependencies outside this checkout, then supply --basic-python and --memory-js.
Basic Memory uses text search: embeddings/model downloads are deliberately excluded.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shared_brain.vault import Vault

FACTS = [
    ("cache", "缓存配置", "缓存服务监听端口是8321。cache service port is 8321."),
    ("backup", "备份流程", "备份数据库之后再执行升级。database backup precedes upgrade."),
    ("timeout", "网络超时", "网络请求超时设置为600秒。network timeout is 600 seconds."),
    ("encoding", "文件编码", "文件编码必须使用UTF-8。file encoding must use UTF-8."),
    ("scope", "项目隔离", "项目隔离要求查询只返回当前项目知识。project isolation scopes retrieval."),
    ("correction", "服务地址", "服务地址是oldhost。service address is oldhost."),
]
QUERIES = [("缓存", "cache"), ("缓存 端口", "cache"), ("备份 升级", "backup"),
           ("网络 超时", "timeout"), ("文件 编码", "encoding"),
           ("cache", "cache"), ("database backup", "backup"),
           ("network timeout", "timeout"), ("file encoding", "encoding"),
           ("project isolation", "scope"), ("不存在的量子打印机", None),
           ("unicornzz", None)]
QUESTIONS = {"cache": ("alpha项目缓存服务监听什么端口？", "8321"),
             "backup": ("alpha项目备份与升级的顺序是什么？", "先备份数据库再升级"),
             "timeout": ("alpha项目网络请求超时多少秒？", "600"),
             "encoding": ("alpha项目文件采用什么编码？", "UTF-8"),
             "scope": ("alpha项目查询允许返回哪些项目知识？", "只返回当前项目"),
             None: ("alpha项目量子打印机的配置是什么？", "资料不足")}


@asynccontextmanager
async def client(command, args, env):
    with open(os.devnull, "w") as errors:
        async with stdio_client(StdioServerParameters(command=command, args=args, env=env), errlog=errors) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                yield session


async def call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    if result.isError:
        raise RuntimeError(f"{name}: {result.content}")
    text = "\n".join(x.text for x in result.content if x.type == "text")
    return json.loads(text) if text.startswith(("{", "[")) else text


def bounded(result):
    """Same 6000-character answer-input budget for every product."""
    return json.dumps(result, ensure_ascii=False)[:6000]


async def measure(name, search, identify, contexts, hydrate=None):
    hits = zh_hits = en_hits = leaks = chars = 0
    false_positives = 0
    timings = []
    for query, expected in QUERIES:
        start = time.perf_counter()
        result = await search(query)
        timings.append((time.perf_counter() - start) * 1000)
        question, answer = QUESTIONS[expected]
        window = result[:5] if isinstance(result, list) else {**result, "entities": result["entities"][:5]} if "entities" in result else result
        answer_context = await hydrate(result) if hydrate else window
        contexts.append({"product": name, "query": query, "question": question, "expected_answer": answer,
                         "expected_id": "alpha/" + expected if expected else None,
                         "retrieval_context": result, "context": bounded(answer_context)})
        ids = identify(result)
        correct = expected is not None and "alpha/" + expected in ids[:5]
        hits += correct
        zh_hits += correct and any("\u4e00" <= c <= "\u9fff" for c in query)
        en_hits += correct and query.isascii()
        false_positives += expected is None and bool(ids)
        leaks += sum(x.startswith("beta/") for x in ids)
        chars += len(json.dumps(result, ensure_ascii=False))
        print(f"  {name} query={query!r} ids={ids[:5]} returned={len(ids)}")
    print(f"RESULT {name}: Hit@5={hits}/10 Chinese={zh_hits}/5 English={en_hits}/5 "
          f"no_answer_FP={false_positives}/2 wrong_project_rows={leaks} "
          f"mean_response_chars={chars / len(QUERIES):.0f} median_ms={statistics.median(timings):.2f}")


async def correction_samples(product, search, contexts, hydrate=None):
    for query in ("服务地址", "service address"):
        result = await search(query)
        window = result[:5] if isinstance(result, list) else {**result, "entities": result["entities"][:5]} if "entities" in result else result
        answer_context = bounded(await hydrate(result) if hydrate else window)
        contexts.append({"product": product, "query": query, "question": "记忆中alpha项目记录的当前服务地址是什么？只问记录，不要求验证网络。",
                         "expected_answer": "newhost", "expected_id": "alpha/correction",
                         "retrieval_context": result, "context": answer_context})
        print("CORRECTION", product, repr(query), "newhost", "newhost" in answer_context, "oldhost", "oldhost" in answer_context)


async def run(options):
    contexts = []
    with tempfile.TemporaryDirectory(prefix="shared-brain-comparison-") as directory:
        root = Path(directory)
        vault = Vault(root / "shared", root / "home")
        paths = {}
        for project in ("alpha", "beta"):
            vault.write(f"项目/{project}/项目.md", {"title": project, "project_id": project, "kind": "project"}, "")
            for key, title, body in FACTS:
                result = vault.save_memory(title, body if project == "alpha" else body.replace("8321", "9444"), project)
                paths[project + "/" + key] = result["path"]
        reverse = {v: k for k, v in paths.items()}

        async def shared_search(query):
            return vault.search(query, "alpha", limit=5)

        print("CONDITIONS 12 identical facts; 2 projects; 10 positive/2 negative queries; "
              "native search, no rewrites, no embeddings, no models; cold sequential timings include IO")
        await measure("shared-brain", shared_search, lambda rows: [reverse[x["path"]] for x in rows], contexts)
        corrected = vault.root / paths["alpha/correction"]
        corrected.write_text(corrected.read_text(encoding="utf-8").replace("oldhost", "newhost"), encoding="utf-8")
        print("EDIT shared-brain", bool(vault.search("newhost", "alpha")), "old", bool(vault.search("oldhost", "alpha")))
        await correction_samples("shared-brain-corrected-immediate", shared_search, contexts)
        fresh = Vault(vault.root, vault.home)
        print("REOPEN shared-brain", bool(fresh.search("newhost", "alpha")))

        memory_env = {**os.environ, "MEMORY_FILE_PATH": str(root / "memory.jsonl")}
        memory_args = [options.memory_js]
        async with client("node", memory_args, memory_env) as session:
            entities = [{"name": f"{project}/{key}", "entityType": project,
                         "observations": [title, body if project == "alpha" else body.replace("8321", "9444")]}
                        for project in ("alpha", "beta") for key, title, body in FACTS]
            await call(session, "create_entities", {"entities": entities})

            async def memory_search(query):
                return await call(session, "search_nodes", {"query": query})

            await measure("server-memory", memory_search, lambda result: [x["name"] for x in result["entities"]], contexts)
            old = FACTS[-1][2]
            await call(session, "delete_observations", {"deletions": [{"entityName": "alpha/correction", "observations": [old]}]})
            await call(session, "add_observations", {"observations": [{"entityName": "alpha/correction", "contents": [old.replace("oldhost", "newhost")]}]})
            print("UPDATE server-memory", len((await memory_search("newhost"))["entities"]), "old_alpha",
                  any(x["name"] == "alpha/correction" for x in (await memory_search("oldhost"))["entities"]))
            await correction_samples("server-memory-corrected-api", memory_search, contexts)
        async with client("node", memory_args, memory_env) as session:
            print("REOPEN server-memory", len((await call(session, "search_nodes", {"query": "newhost"}))["entities"]))
        print("BOUNDARY server-memory: no native project/limit args; JSONL, no Markdown edit tested; "
              "a separate scoped-file plus split-query variant is measured next")

        scoped_env = {**os.environ, "MEMORY_FILE_PATH": str(root / "alpha-memory.jsonl")}
        async with client("node", memory_args, scoped_env) as session:
            await call(session, "create_entities", {"entities": [e for e in entities if e["entityType"] == "alpha"]})

            async def scoped_split(query):
                found = {}
                for term in query.split():
                    result = await call(session, "search_nodes", {"query": term})
                    found.update((e["name"], e) for e in result["entities"])
                return {"entities": list(found.values()), "relations": []}

            await measure("server-memory-scoped-split", scoped_split,
                          lambda result: [x["name"] for x in result["entities"]], contexts)

        basic_env = {**os.environ, "BASIC_MEMORY_CONFIG_DIR": str(root / "basic-config"),
                     "BASIC_MEMORY_HOME": str(root / "basic-main"), "BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED": "false",
                     "BASIC_MEMORY_LOGFIRE_ENABLED": "false"}
        basic_args = ["-c", "from basic_memory.cli.main import app; app()", "mcp"]
        async with client(options.basic_python, basic_args, basic_env) as session:
            for project in ("alpha", "beta"):
                await call(session, "create_memory_project", {"project_name": project, "project_path": str(root / project)})
                for key, title, body in FACTS:
                    await call(session, "write_note", {"title": key, "directory": "facts", "project": project,
                                                       "content": title + "\n\n" + (body if project == "alpha" else body.replace("8321", "9444"))})

            async def basic_search(query):
                return await call(session, "search_notes", {"query": query, "project": "alpha", "search_type": "text",
                                                             "page_size": 5, "output_format": "json"})

            identify = lambda result: ["alpha/" + x["title"] for x in result["results"]]

            async def basic_hydrate(result):
                docs = []
                for row in result["results"][:5]:
                    identifier = row.get("file_path") or row["permalink"]
                    content = await call(session, "read_note", {"identifier": identifier, "project": "alpha"})
                    docs.append({"title": row["title"], "path": identifier, "text": content})
                return docs

            await measure("basic-memory-text", basic_search, identify, contexts, basic_hydrate)
            sample = next(x for x in contexts if x["product"] == "basic-memory-text" and x["query"] == "缓存")
            print("READ basic-memory raw_search_has_8321", "8321" in json.dumps(sample["retrieval_context"]),
                  "hydrated_has_8321", "8321" in sample["context"])
            note = next((root / "alpha").rglob("correction.md"))
            note.write_text(note.read_text(encoding="utf-8").replace("oldhost", "newhost"), encoding="utf-8")
            print("EDIT basic-memory immediate", bool((await basic_search("newhost"))["results"]))
            for seconds in range(1, 6):
                await asyncio.sleep(1)
                if (await basic_search("newhost"))["results"]:
                    print("EDIT basic-memory watcher_visible_within_seconds", seconds)
                    break
            else:
                print("EDIT basic-memory not_visible_after_5_seconds")
            await correction_samples("basic-memory-corrected-watcher-window", basic_search, contexts, basic_hydrate)
        sync = subprocess.run([options.basic_python, "-c", "from basic_memory.cli.main import app; app()", "reindex", "--search", "--project", "alpha"],
                              env=basic_env, capture_output=True, text=True, encoding="utf-8")
        print("SYNC basic-memory exit", sync.returncode)
        if sync.returncode:
            print(sync.stderr[-1200:])
        async with client(options.basic_python, basic_args, basic_env) as session:
            result = await call(session, "search_notes", {"query": "newhost", "project": "alpha", "search_type": "text", "output_format": "json"})
            old = await call(session, "search_notes", {"query": "oldhost", "project": "alpha", "search_type": "text", "output_format": "json"})
            print("EDIT/REOPEN basic-memory after sync", bool(result["results"]), "old", bool(old["results"]))
            await correction_samples("basic-memory-corrected-reindex", basic_search, contexts, basic_hydrate)
    return contexts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basic-python", required=True)
    parser.add_argument("--memory-js", required=True)
    parser.add_argument("--contexts", action="store_true", help="Print raw contexts to stdout for model answer evaluation")
    options = parser.parse_args()
    contexts = asyncio.run(run(options))
    if options.contexts:
        print("CONTEXTS " + json.dumps(contexts, ensure_ascii=False))
