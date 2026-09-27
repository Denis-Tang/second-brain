import asyncio
import json
from shared_brain.mcp_server import create_server
from test_service import setup


def test_mcp_schema_and_real_local_workflow(tmp_path):
    service, context, _ = setup(tmp_path)
    server = create_server(service.settings.home)
    async def exercise():
        tools = {t.name: t for t in await server.list_tools()}
        assert set(tools) == {"bootstrap", "search", "save", "feedback", "status"}
        assert tools["save"].inputSchema["required"] == ["context"]
        result = await server.call_tool("bootstrap", {"cwd": str(tmp_path / "workspace"), "session_id": "s1", "agent": "Codex"})
        assert not result[1]["needs_choice"] if isinstance(result, tuple) else not json.loads(result[0].text)["needs_choice"]
        saved = await server.call_tool("save", {"context": context, "summary": "MCP结果", "errors_reviewed": True})
        data = saved[1] if isinstance(saved, tuple) else json.loads(saved[0].text)
        assert data["summary"]["path"].startswith("会话总结/")
    asyncio.run(exercise())
