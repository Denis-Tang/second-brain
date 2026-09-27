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

    @server.tool(description="Start once per root session. Unknown workspace requires user choice: new (project_name), existing (project ID) or independent (remembered for this path). Reuse host session_id; workspace_root overrides Git detection. choice may explicitly change an existing binding.")
    def bootstrap(cwd: str, session_id: str, agent: str, workspace_root: str = "", choice: str = "", project: str = "", project_name: str = "", task_id: str = "") -> dict:
        return service.bootstrap(cwd, session_id, agent, workspace_root, choice, project, project_name, task_id)

    @server.tool(description="Local scoped search. Pass project ID from bootstrap, target/method/environment for applicable failures. Unknown conditions need checking; ask about conflicts only when the current task requires a choice.")
    def search(query: str, project: str = "", limit: int = 5, target: str = "", method: str = "", environment: dict[str, str] | None = None) -> dict:
        return service.search(query, project, limit, target, method, environment)

    @server.tool(description="Root-only meaningful stage save. summary is the full current session summary, replacing that session's previous text. Project progress updates immediately; task_id/decision only when useful. Separate real errors, no raw tool outputs/secrets. At closeout review errors and set errors_reviewed=true. Explicit user project decisions may set active/paused/completed; no file moves.")
    def save(context: Context, summary: str = "", goal: str = "", progress: str = "", next_actions: list[str] | None = None,
             task_id: str = "", memory: MemoryInput | None = None, errors: list[ErrorInput] | None = None, errors_reviewed: bool = False,
             project_status: str = "", decision: dict[str, str] | None = None) -> dict:
        return service.save(context, summary, goal, progress, next_actions, task_id, memory, errors, errors_reviewed, project_status, decision)

    @server.tool(description="After checking a real successful outcome, link it to an earlier error. observation needs target, method, environment, evidence. Comparable conditions retire current warning; different methods add a workaround; unknown conditions retain both observations.")
    def feedback(context: Context, error_session_id: str, error_id: str, observation: dict) -> dict:
        return service.feedback(context, error_session_id, error_id, observation)

    @server.tool(description="Read readiness, pending maintenance and monthly budget usage. Does not invoke a model.")
    def status() -> dict:
        return service.status()
    return server


def run(home: Path | None = None):
    create_server(home).run(transport="stdio")
