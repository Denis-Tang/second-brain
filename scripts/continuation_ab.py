"""Paired native-compaction/fresh-memory development in disposable workspaces."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import queue
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from shared_brain.service import BrainService

PYTHON = REPO / ".venv/Scripts/python.exe"
CODEX = shutil.which("codex.exe")
MODEL = "gpt-6.1-sol"

TASKS = [
    ("clear_project_progress", "为 save 支持明确清空项目进度。我们已经决定：省略 progress 保留旧值，显式 progress='' 清空进度，非空文本更新。服务与 MCP 参数默认值一致。保留手写项目正文、独立会话规则和现有错误保存逻辑；不新增工具或配置。", """
s.save(c,summary='阶段',progress='旧进度')
s.save(c,summary='阶段',next_actions=['下一步'])
assert s._vault().project(start['project'])['progress']=='旧进度'
s.save(c,summary='阶段',progress='')
assert s._vault().project(start['project'])['progress']==''
assert s._vault().project(start['project'])['body'].strip()=='手写说明'
from shared_brain.mcp_server import create_server
tool=create_server(root/'home')._tool_manager.get_tool('save').fn
s.save(c,summary='阶段',progress='MCP旧进度')
tool(c,summary='阶段')
assert s._vault().project(start['project'])['progress']=='MCP旧进度'
tool(c,summary='阶段',progress='')
assert s._vault().project(start['project'])['progress']==''
"""),
    ("patch_existing_task", "修复重复保存任务时的部分更新。我们已经决定：save(task_id='t1') 省略 goal/progress/next_actions 时保留该任务旧目标、正文和下一步；提供 next_actions=[] 则清空任务下一步，显式 progress='' 则清空任务正文；新任务允许无正文。保留项目正文和同会话总结规则。不要新增 MCP 工具、配置或模块。", """
s.save(c,summary='阶段',task_id='t1',goal='原目标',progress='任务正文',next_actions=['原下一步'])
s.save(c,summary='阶段',task_id='t1')
v=s._vault(); card=v.project(start['project'])
p=v.root/Path(card['path']).parent/'任务/t1.md'
m,b=v.read(p)
assert m['goal']=='原目标' and b.strip()=='任务正文' and m['next_actions']==['原下一步']
s.save(c,summary='阶段',task_id='t1',progress='',next_actions=[])
m,b=v.read(p); assert not b.strip() and m['next_actions']==[] and m['goal']=='原目标'
assert v.project(start['project'])['body'].strip()=='手写说明'
"""),
    ("bootstrap_task_id", "统一 bootstrap 读取任务和 save_task 保存任务的 ID 规则。我们已经决定：task_id 必须为 1 到 80 位字母数字点下划线横线且首位字母数字，空字符串代表不查任务；../路径、斜杠、绝对路径等无效 ID 应在 bootstrap 边界报 ValueError。合法但不存在的任务继续返回 None，不创建文件。保留稳定会话工作区、项目路径匹配和全局提示词加载。直接改现有代码，不新增模块或配置。", """
for bad in ['../项目','/absolute','bad/name','.','x'*81]:
    try: s.bootstrap(str(work),'test','Codex',task_id=bad)
    except ValueError: pass
    else: raise AssertionError('accepted invalid task ID '+bad)
assert s.bootstrap(str(work),'test','Codex',task_id='missing')['task'] is None
s.save(c,summary='阶段',task_id='valid-1',goal='合法任务',progress='已创建')
assert s.bootstrap(str(work),'test','Codex',task_id='valid-1')['task']['progress'].strip()=='已创建'
assert s._vault().project(start['project'])['body'].strip()=='手写说明'
"""),
    ("preserve_summary_title", "修复同会话更新总结时丢失标题。我们已经决定：首次 save(summary='正文',goal='标题') 生成标题；后续省略 goal 或 goal='' 保留这份总结的原标题；显式非空 goal 更新标题。没有旧标题时仍用“会话总结”。同一 session 始终更新原总结文件，不新增文件，项目卡手写正文保留。只改已有函数，不新增配置。", """
first=s.save(c,summary='第一阶段',goal='原标题')['summary']['path']
second=s.save(c,summary='第二阶段')['summary']['path']
v=s._vault(); m,b=v.read(v.root/first)
assert first==second and m['title']=='原标题' and b.strip()=='第二阶段'
s.save(c,summary='第三阶段',goal=''); m,b=v.read(v.root/first)
assert m['title']=='原标题'
s.save(c,summary='完成',goal='新标题'); m,b=v.read(v.root/first)
assert m['title']=='新标题' and v.project(start['project'])['body'].strip()=='手写说明'
assert len(list((v.root/'会话总结').rglob('*.md')))==1
"""),
    ("bootstrap_agent_name", "将 Agent 名称校验提前到 bootstrap 的外部输入边界。我们已经决定：Agent 名称必须匹配 save_session 已有的 1到60位文字、数字、下划线和横线规则。斜杠、空白、超过60位均抛 ValueError，不应等到 save 时才失败，也不为无效会话保存 session 状态。合法中文名称继续可 bootstrap/save。不改变项目归属、会话稳定工作区或总结目录布局，不新增模块。", """
from shared_brain.vault import fingerprint
for i,bad in enumerate(['bad/name','has space','a'*61,'']):
    sid='invalid-'+str(i)
    try: s.bootstrap(str(work),sid,bad)
    except ValueError: pass
    else: raise AssertionError('accepted invalid agent '+bad)
    assert s._vault().state('session:'+fingerprint(sid)) is None
good=s.bootstrap(str(work),'valid-agent','中文代理_1')
s.save({'role':'root','session_id':'valid-agent','root_session_id':'valid-agent'},summary='正常保存')
assert good['project']==start['project']
"""),
]


class Codex:
    def __init__(self):
        self.process = subprocess.Popen([CODEX, "app-server", "-c", "mcp_servers={}",
            "-c", "features.hooks=false", "-c", "features.memories=false"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8")
        self.queue = queue.Queue()
        self.pending = []
        self.counter = 0
        self.usage = {}
        threading.Thread(target=self.read, daemon=True).start()
        self.call("initialize", {"clientInfo":{"name":"shared-brain-ab","version":"0.1"},
                                 "capabilities":{"experimentalApi":True}})
        self.send({"method":"initialized","params":{}})

    def read(self):
        for line in self.process.stdout:
            self.queue.put(json.loads(line))

    def send(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=False)+"\n")
        self.process.stdin.flush()

    def call(self, method, params):
        self.counter += 1
        ident = self.counter
        self.send({"id":ident,"method":method,"params":params})
        started=time.monotonic()
        while True:
            if time.monotonic()-started>60:
                raise TimeoutError("native request did not return within one minute")
            message = self.queue.get(timeout=max(0,60-(time.monotonic()-started)))
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message["result"]
            self.pending.append(message)

    def finish(self, thread):
        started = time.monotonic()
        tools, compacted, usage, answer = 0, False, None, ""
        while True:
            if time.monotonic()-started>600:
                raise TimeoutError("turn exceeded the ten-minute pilot limit")
            message = self.pending.pop(0) if self.pending else self.queue.get(timeout=max(0,600-(time.monotonic()-started)))
            params = message.get("params", {})
            if params.get("threadId") != thread:
                continue
            method = message.get("method")
            if method == "item/completed":
                item = params.get("item", {})
                tools += item.get("type") in {"commandExecution","mcpToolCall","fileChange"}
                compacted |= item.get("type") == "contextCompaction"
                if item.get("type") == "agentMessage":
                    answer = item.get("text", "")
            elif method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage")
                self.usage[thread] = usage
            elif method == "turn/completed":
                turn = params["turn"]
                return dict(status=turn["status"], error=turn.get("error"), tools=tools, answer=answer,
                            compacted=compacted, usage=usage, seconds=time.monotonic()-started)

    def start(self, work):
        mcp = {"shared_brain":{"command":str(PYTHON),"args":["-m","shared_brain","--home",str(work/"brain-home"),"mcp"],"cwd":str(work),"env":{"PYTHONPATH":str(work/"src")},"default_tools_approval_mode":"approve"}}
        result = self.call("thread/start", {"model":MODEL,"cwd":str(work),"approvalPolicy":"never",
            "sandbox":"danger-full-access","ephemeral":True,
             "config":{"mcp_servers":mcp,"model_reasoning_effort":"low"},
            "baseInstructions":"你在隔离的开发实验副本中工作。只访问当前工作目录及其文件，凭据、用户配置和其他项目不能读取或修改。直接完成当前任务，不调用子代理，不提交或发布。项目开发时可使用 Shared Brain 五工具。"})
        if result["model"] != MODEL:
            raise RuntimeError("server selected a different model")
        return result["thread"]["id"]

    def turn(self, thread, prompt):
        before = self.usage.get(thread, {}).get("total", {})
        self.call("turn/start", {"threadId":thread,"effort":"low",
            "input":[{"type":"text","text":prompt}]})
        result = self.finish(thread)
        if result["usage"]:
            result["continuation_tokens"] = {k:v-before.get(k,0) for k,v in result["usage"]["total"].items()}
        return result

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=15)


def initialize(work):
    s = BrainService(work/"brain-home")
    s.initialize(str(work/"vault"))
    cards = s.projects()["projects"]
    if cards:
        s.configure_project("接续开发实验",[str(work)],cards[0]["project_id"])
    else:
        s.configure_project("接续开发实验",[str(work)])
    return s


def make_workspace(work):
    work.mkdir()
    for folder in ("src/shared_brain","tests"):
        for source in (REPO/folder).glob("*.py"):
            target=work/folder/source.name; target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
    shutil.copy2(REPO/"pyproject.toml",work/"pyproject.toml")
    service=initialize(work)
    service.save_global_prompt("只在当前实验工作区执行本轮任务；项目手写说明必须保留。")


def assess(work, assertions):
    # The acceptance assertions live outside each agent's workspace.
    setup = """
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'src'))
from shared_brain.service import BrainService
with tempfile.TemporaryDirectory() as td:
    root=Path(td); work=root/'workspace';work.mkdir()
    s=BrainService(root/'home');s.initialize(str(root/'vault'));s.configure_project('测试',[str(work)])
    start=s.bootstrap(str(work),'test','Codex');c={'role':'root','session_id':'test','root_session_id':'test'}
    v=s._vault();card=v.project(start['project']);m,b=v.read(v.root/card['path']);v.write(card['path'],m,'手写说明')
"""
    code=setup+"\n".join("    "+line for line in assertions.strip().splitlines())
    completed=subprocess.run([str(PYTHON),"-c",code,str(work)],capture_output=True,text=True,encoding="utf-8")
    regression=subprocess.run([str(PYTHON),"-m","pytest","tests/test_service.py","-q"],
                              cwd=work,capture_output=True,text=True,encoding="utf-8")
    return dict(acceptance=completed.returncode==0,regression=regression.returncode==0,
                acceptance_output=(completed.stderr or completed.stdout)[-1400:],
                regression_output=regression.stdout[-600:])


def identity(thread):
    return "宿主真实根身份为 session_id="+thread+"，root_session_id 同值，role=root，agent=Codex；cwd 为当前目录。"


def run_pair(index, root, code_review=False):
    api=Codex();results=[]
    try:
        name,requirements,assertions=TASKS[index%len(TASKS)]
        history_band=(index//len(TASKS))%3
        noise="".join(f"历史讨论{i}：曾考虑增加远程同步、向量数据库和插件配置；这些方案已撤回，当前不采用。\n" for i in range((0,40,160)[history_band]))
        a=root/f"{index:02d}-{name}-A";b=root/f"{index:02d}-{name}-B"
        make_workspace(a);ta=api.start(a)
        preparation = ("先 bootstrap，再用一次 shell 调用批量读取 service.py、vault.py、mcp_server.py 中与最终决定直接相关的函数，不通读全文；最后 save。总结保留函数位置、现有行为、修改方案和全部最终决定。" if code_review else "本轮只调用 bootstrap 和 save 两个工具；不读代码。")
        prefix=noise+"\n以下是最终决定，优先于已撤回的历史方案："+requirements+"\n"+identity(ta)+"\n这是开发阶段断点。"+preparation+"save 保存完整最终决定、进度和下一步及 errors_reviewed=true。最终决定须完整保留，已撤回方案不可变成需求。不修改源码、不运行测试、不提问，不添加设计文档。"
        prepared=api.turn(ta,prefix)
        if prepared["status"]!="completed" or not list((a/"vault/会话总结").rglob("*.md")):
            raise RuntimeError("preparation did not save a stage summary")
        print(f"PAIR {index} {name} history_band={history_band} prepared",flush=True)
        shutil.copytree(a,b);initialize(b);tb=api.start(b)
        before=api.usage[ta]["total"].copy()
        compact_started=time.monotonic();api.call("thread/compact/start",{"threadId":ta})
        compact=api.finish(ta);compact_seconds=time.monotonic()-compact_started
        compact_tokens={k:v-before.get(k,0) for k,v in api.usage[ta]["total"].items()}
        if not compact["compacted"] or compact["status"]!="completed":
            raise RuntimeError("native compaction not verified")
        order=[("A",a,ta),("B",b,tb)]
        if index%2: order.reverse()
        for arm,work,thread in order:
            prompt="继续这个项目，完成上一阶段已经确定的下一项改动。先 bootstrap，按需读取项目会话索引、最后总结和 search。只执行最终决定。"+identity(thread)+"\n测试 Python 为 "+str(PYTHON)+"。直接编辑 src 和相关 tests，不改变既有验收意义。源码只需 service.py、vault.py、mcp_server.py 中相关函数；用一次 shell 调用批量读取所需函数，不通读全文。只跑 tests/test_service.py 一次；本副本没有 Web 和分发包。完成后保存简短总结。省用量，尽量在6次工具调用内完成，不添加无关模块、配置或接口。"
            measured=api.turn(thread,prompt);checks=assess(work,assertions)
            tokens=measured.get("continuation_tokens",{})
            if arm=="A":
                tokens={k:tokens.get(k,0)+v for k,v in compact_tokens.items()}
            row=dict(pair=index,task=name,history_band=history_band,arm=arm,
                     status=measured["status"],seconds=measured["seconds"]+(compact_seconds if arm=="A" else 0),
                     input_tokens=tokens.get("inputTokens",0),cached_input_tokens=tokens.get("cachedInputTokens",0),
                     output_tokens=tokens.get("outputTokens",0),tools=measured["tools"],**checks)
            results.append(row)
            print("RESULT",json.dumps(row,ensure_ascii=False),flush=True)
    finally:
        api.close()
    return results


def run(pairs,workers,code_review=False):
    with tempfile.TemporaryDirectory(prefix="shared-brain-continuation-") as td:
        print(f"model={MODEL}; effort=low; pairs={pairs}; workers={workers}; code_review={code_review}",flush=True)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            groups=list(pool.map(lambda i:run_pair(i,Path(td),code_review),range(pairs)))
        rows=[row for group in groups for row in group]
        for arm in ("A","B"):
            subset=[r for r in rows if r["arm"]==arm]
            print("TOTAL",arm,json.dumps({
                "n":len(subset),"acceptance":sum(r["acceptance"] for r in subset),
                "regression":sum(r["regression"] for r in subset),
                "input_tokens":sum(r["input_tokens"] for r in subset),
                "uncached_input_tokens":sum(r["input_tokens"]-r["cached_input_tokens"] for r in subset),
                "output_tokens":sum(r["output_tokens"] for r in subset),
                "sum_seconds":round(sum(r["seconds"] for r in subset),1),
                "median_seconds":round(statistics.median(r["seconds"] for r in subset),1),
            }),flush=True)
        ordered=[sorted(group,key=lambda r:r["arm"]) for group in groups]
        wins=sum(b["acceptance"] and not a["acceptance"] for a,b in ordered)
        losses=sum(a["acceptance"] and not b["acceptance"] for a,b in ordered)
        print(f"PAIRED fresh-only={wins}; compact-only={losses}",flush=True)
        return rows


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs",type=int,default=15)
    parser.add_argument("--workers",type=int,default=2)
    parser.add_argument("--code-review",action="store_true",help="Read relevant source before saving the breakpoint")
    args=parser.parse_args()
    run(args.pairs,args.workers,args.code_review)
