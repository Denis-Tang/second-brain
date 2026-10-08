"""MCP exposes the local application workflow without model or shell execution."""
from pathlib import Path
from typing_extensions import TypedDict, NotRequired
from mcp.server.fastmcp import FastMCP
from .service import BrainService


class Context(TypedDict):
    role: str
    session_id: str
    root_session_id: str


class MemoryInput(TypedDict):
    title: str
    body: str
    verified: NotRequired[bool]
    evidence: NotRequired[str]
    conditions: NotRequired[dict[str, str]]


class ChangeInput(TypedDict):
    object: str
    action: str
    location: str
    state: str
    evidence: NotRequired[str]
    conditions: NotRequired[dict[str, str]]


class ErrorInput(TypedDict):
    target: str
    method: str
    symptom: str
    id: NotRequired[str]
    environment: NotRequired[dict[str, str]]
    attempts: NotRequired[str]
    workaround: NotRequired[str]
    impact: NotRequired[str]
    global_scope: NotRequired[bool]


def create_server(home: Path | None = None) -> FastMCP:
    service = BrainService(home)
    server = FastMCP("Shared Brain")

    @server.tool(description="Start each root session and refresh after project configuration changes. Reuse host session_id; workspace_root overrides startup cwd. Configured paths match the most specific project; unmatched paths are independent. Provide a brief current task (up to 500 characters) when known to select a few relevant knowledge/skill records. Always follow the full nonempty global_prompt, edited only by the user. Drafts are excluded; the user supplies them when needed.")
    def bootstrap(cwd: str, session_id: str, agent: str, workspace_root: str = "", task_id: str = "", task: str = "") -> dict:
        return service.bootstrap(cwd, session_id, agent, workspace_root, task_id, task=task)

    @server.tool(description="Search knowledge, skills and applicable errors locally. project=ID searches that project and, for a child project, its parent knowledge; project='independent' searches the public object pool; empty project or 'all' searches all projects and the public pool. Independent sessions default to all; project sessions pass the bootstrap project ID. mode=auto detects Chinese prose versus code-shaped queries (identifiers, paths, file names); pass mode='text' or mode='code' to override and the response reports the mode used. Check source project, conditions and evidence. Drafts are excluded. Pass target/method/environment for failures; unknown conditions need checking.")
    def search(query: str, project: str = "", limit: int = 5, target: str = "", method: str = "",
               environment: dict[str, str] | None = None, mode: str = "auto") -> dict:
        return service.search(query, project, limit, target, method, environment, mode)

    @server.tool(description="Root-only stage save. Project sessions save a full current summary and progress, with optional tasks, decisions and verified memory. Independent sessions also save a full summary, without creating a project, task, decision or standalone memory. All sessions record actual installation/removal, tool/skill/model configuration and computer environment changes in changes: object, action, exact location, current state, optional evidence/conditions; ordinary source edits stay in project records. Changes update public object files and history. Record real errors separately. At closeout review changes and errors and set errors_reviewed=true together with the full summary, including independent sessions. Never edit the user's global prompt or drafts automatically. Generated skills are records, not installed or executed.")
    def save(context: Context, summary: str = "", goal: str = "", progress: str = "", next_actions: list[str] | None = None,
             task_id: str = "", memory: MemoryInput | None = None, errors: list[ErrorInput] | None = None, errors_reviewed: bool = False,
             project_status: str = "", decision: dict[str, str] | None = None, changes: list[ChangeInput] | None = None) -> dict:
        return service.save(context, summary, goal, progress, next_actions, task_id, memory, errors, errors_reviewed, project_status, decision, changes=changes)

    @server.tool(description="After checking a real successful outcome, link it to an earlier error. observation needs target, method, environment, evidence. Comparable conditions retire current warning; different methods add a workaround; unknown conditions retain both observations.")
    def feedback(context: Context, error_session_id: str, error_id: str, observation: dict) -> dict:
        return service.feedback(context, error_session_id, error_id, observation)

    @server.tool(description="Read readiness, pending maintenance and monthly budget usage. Does not invoke a model.")
    def status() -> dict:
        return service.status()
    return server


def run(home: Path | None = None):
    create_server(home).run(transport="stdio")
