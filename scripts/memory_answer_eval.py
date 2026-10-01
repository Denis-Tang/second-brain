"""Grounded answers from real product retrieval, using the signed-in Codex account."""
import argparse
import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import tempfile

from continuation_ab import Codex, MODEL
from memory_comparison import run


def correct(answer, expected):
    if expected != "资料不足" and any(word in answer for word in ("资料不足", "无法确定", "未提供")):
        return False
    if expected == "先备份数据库再升级":
        return "备份" in answer and "升级" in answer and answer.index("备份") < answer.index("升级")
    if expected == "只返回当前项目":
        return ("当前项目" in answer or "alpha" in answer.lower()) and "所有项目" not in answer
    return expected.casefold() in answer.casefold()


def answer_sample(sample):
    with tempfile.TemporaryDirectory(prefix="shared-brain-answer-") as td:
        api = Codex()
        try:
            thread = api.call("thread/start", {
                "model": MODEL, "cwd": td, "approvalPolicy": "never", "sandbox": "read-only",
                "ephemeral": True, "config": {"mcp_servers": {}, "model_reasoning_effort": "low"},
                "baseInstructions": "只根据本题给出的记忆资料回答。只能使用alpha项目资料。资料不足就说资料不足。不要调用任何工具，不读取文件或网络。回答简短。",
            })
            if thread["model"] != MODEL:
                raise RuntimeError("server selected a different model")
            ident = thread["thread"]["id"]
            api.call("turn/start", {"threadId": ident, "effort": "low",
                "input": [{"type": "text", "text": sample["question"] + "\n记忆资料：\n" + sample["context"]}],
                "outputSchema": {"type": "object", "properties": {"answer": {"type": "string"}},
                                 "required": ["answer"], "additionalProperties": False},
            })
            result = api.finish(ident)
            answer = json.loads(result["answer"])["answer"]
            passed = result["status"] == "completed" and correct(answer, sample["expected_answer"])
            usage = result["usage"]["total"]
            print("ANSWER", sample["product"], repr(sample["query"]), passed, repr(answer), flush=True)
            return {"product": sample["product"], "passed": passed, "answer": answer,
                    "input": usage["inputTokens"], "uncached": usage["inputTokens"] - usage["cachedInputTokens"],
                    "output": usage["outputTokens"], "tools": result["tools"]}
        finally:
            api.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basic-python", required=True)
    parser.add_argument("--memory-js", required=True)
    parser.add_argument("--corrections-only", action="store_true")
    options = parser.parse_args()
    samples = asyncio.run(run(options))
    if options.corrections_only:
        samples = [sample for sample in samples if "corrected" in sample["product"]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(answer_sample, samples))
    groups = defaultdict(list)
    for row in results:
        groups[row["product"]].append(row)
    for product, rows in groups.items():
        print("ANSWER_TOTAL", product, sum(r["passed"] for r in rows), "/", len(rows), flush=True)
    print("MODEL_USAGE", MODEL, "effort=low", json.dumps({key: sum(r[key] for r in results)
          for key in ("input", "uncached", "output", "tools")}), flush=True)
