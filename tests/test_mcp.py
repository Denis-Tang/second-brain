import asyncio
import json
import os
import struct
import subprocess
from pathlib import Path
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from shared_brain.mcp_server import create_server
from test_service import setup


def test_mcp_schema_and_real_local_workflow(tmp_path):
    service, context, _ = setup(tmp_path)
    assert set(service.mcp_config()["mcpServers"]) == {"shared_brain"}
    server = create_server(service.settings.home)
    async def exercise():
        tools = {t.name: t for t in await server.list_tools()}
        assert set(tools) == {"bootstrap", "search", "save", "feedback", "status"}
        assert tools["save"].inputSchema["required"] == ["context"]
        assert not {"choice", "project", "project_name"} & tools["bootstrap"].inputSchema["properties"].keys()
        assert tools["bootstrap"].inputSchema["properties"]["task"]["default"] == ""
        assert tools["search"].inputSchema["properties"]["mode"]["default"] == "auto"
        change = tools["save"].inputSchema["$defs"]["ChangeInput"]
        assert set(change["required"]) == {"object", "action", "location", "state"}
        result = await server.call_tool("bootstrap", {"cwd": str(tmp_path / "workspace"), "session_id": "s1", "agent": "Codex", "task": "验证MCP结果"})
        assert result[1]["project_directory"] if isinstance(result, tuple) else json.loads(result[0].text)["project_directory"]
        saved = await server.call_tool("save", {"context": context, "summary": "MCP结果", "errors_reviewed": True})
        data = saved[1] if isinstance(saved, tuple) else json.loads(saved[0].text)
        assert data["summary"]["path"].startswith("会话总结/")
    asyncio.run(exercise())


def test_mcp_independent_summary_and_public_changes(tmp_path):
    service, _, _ = setup(tmp_path)
    server = create_server(service.settings.home)
    (tmp_path / "standalone").mkdir()
    context = {"role": "root", "session_id": "independent", "root_session_id": "independent"}

    async def call(name, arguments):
        result = await server.call_tool(name, arguments)
        assert not getattr(result, "isError", False)
        return result[1] if isinstance(result, tuple) else json.loads(result[0].text)

    async def exercise():
        boot = await call("bootstrap", {"cwd": str(tmp_path / "standalone"), "session_id": "independent", "agent": "Codex"})
        assert boot["independent"]
        empty = await call("save", {"context": context, "errors_reviewed": True})
        assert "summary" not in empty
        assert not list((tmp_path / "vault" / "会话总结").rglob("*.md"))
        saved = await call("save", {"context": context, "summary": "安装结果已验证。", "changes": [{
            "object": "MCP测试模型", "action": "安装", "location": str(tmp_path / "models"),
            "state": "已安装", "evidence": "模型成功加载", "conditions": {"runtime": "本机"},
        }], "errors_reviewed": True})
        assert saved["summary"]["path"].startswith("会话总结/")
        meta, body = service._vault().read(tmp_path / "vault" / saved["summary"]["path"])
        assert meta["project"] == "" and body.strip() == "安装结果已验证。"
        result = await call("search", {"query": "MCP测试模型", "project": "independent"})
        assert result["results"] and "独立项目/独立知识/" in result["results"][0]["path"]
        boot = await call("bootstrap", {"cwd": str(tmp_path / "standalone"), "session_id": "independent", "agent": "Codex", "task": "MCP测试模型"})
        assert boot["relevant"]

    asyncio.run(exercise())


def test_windowed_executable_preserves_stdio_mcp(tmp_path):
    executable = Path(os.environ.get("SHARED_BRAIN_TEST_EXE", str(Path(__file__).resolve().parents[1] / "dist/shared-brain/shared-brain.exe")))
    if not executable.exists():
        pytest.skip("Build the Windows distribution to test its stdio transport")
    with executable.open("rb") as binary:
        binary.seek(0x3C)
        pe_offset = struct.unpack("<I", binary.read(4))[0]
        binary.seek(pe_offset + 24 + 68)
        assert struct.unpack("<H", binary.read(2))[0] == 2  # Windows GUI subsystem, no console.
    version = subprocess.run([str(executable), "--version"], capture_output=True, text=True,
                             encoding="utf-8", timeout=15)
    assert version.returncode == 0 and json.loads(version.stdout)["version"]
    service, context, started = setup(tmp_path)

    async def exercise():
        params = StdioServerParameters(command=str(executable), args=["--home", str(service.settings.home), "mcp"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {"bootstrap", "save", "search", "feedback", "status"}
                assert "changes" in next(t for t in tools.tools if t.name == "save").inputSchema["properties"]
                assert "task" in next(t for t in tools.tools if t.name == "bootstrap").inputSchema["properties"]
                assert "mode" in next(t for t in tools.tools if t.name == "search").inputSchema["properties"]
                boot = await session.call_tool("bootstrap", {"cwd": str(tmp_path / "workspace"), "session_id": "s1", "agent": "Codex"})
                assert not boot.isError
                assert json.loads(boot.content[0].text)["project"] == started["project"]
                saved = await session.call_tool("save", {"context": context, "summary": "无控制台管道验证", "errors_reviewed": True})
                assert not saved.isError
                path = json.loads(saved.content[0].text)["summary"]["path"]
                assert "无控制台管道验证" in (tmp_path / "vault" / path).read_text(encoding="utf-8")

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
