// Codex and Claude Code command hooks. No transcript or tool-output ingestion.
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const [host, command, home] = process.argv.slice(2);
try {
  const input = JSON.parse(readFileSync(0, "utf8"));
  const event = input.hook_event_name;
  const child = event?.startsWith("Subagent") || input.agent_id || input.parent_session_id;
  const contextOutput = (text) => ({ hookSpecificOutput: { hookEventName: event, additionalContext: text } });
  let output = {};
  if (child) {
    if (event === "SubagentStart") output = contextOutput("你是子代理：只向根代理回传成果、错误与证据，不调用 Shared Brain save/feedback，不独立写共享总结。");
  } else if (input.session_id && ["SessionStart", "UserPromptSubmit", "Stop"].includes(event)) {
    const context = { role: "root", session_id: input.session_id, root_session_id: input.session_id };
    if (event === "SessionStart") {
      const args = { cwd: input.cwd, workspace_root: input.cwd, session_id: input.session_id, agent: host };
      output = contextOutput(`Shared Brain 宿主根身份 context=${JSON.stringify(context)}。
调用 mcp__shared_brain__bootstrap，参数 ${JSON.stringify(args)}。项目由应用的项目配置决定，未匹配路径直接作为独立会话；项目资料目录见 project_directory，不询问或自行创建项目。
任务明确后带 task=当前任务描述重新 bootstrap，描述不超过500字符；遵循全文 global_prompt，参考少量 relevant，其他按需 search。项目默认传项目 ID，独立会话留空搜全部；project="independent" 只搜公共池，"all" 搜全部。核对来源与条件。草稿只由用户提供，不自动检索或整理。
项目会话 save 完整 summary 与项目进度；独立会话不写总结、卡、决策或单独 memory。所有会话发生安装/卸载、工具配置、skills、模型、PATH/代理等环境变更时，save changes 数组，每项 object/action/location/state，附实际 evidence/conditions，更新公共对象状态与历史；普通源码修改留在项目记录。
收尾核对变更与实际错误后 errors_reviewed=true；独立会话无事项可只 save(context,errors_reviewed=true)，不生成空文件。全局提示词仅用户编辑，生成技能不自动安装执行。同一会话复用身份，子代理不独立保存。`);
    } else if (!(event === "Stop" && input.stop_hook_active)) {
      const result = JSON.parse(execFileSync(command, ["--home", home, "hook"], {
        input: JSON.stringify({ ...context, event: event === "Stop" ? "closeout" : "turn", turn_id: input.turn_id }),
        encoding: "utf8", windowsHide: true, timeout: 3000, maxBuffer: 16384,
      }));
      if (event === "Stop" && result.decision === "block") output = { decision: "block", reason: result.reason };
    }
  }
  process.stdout.write(JSON.stringify(output));
} catch {
  // Hook I/O is a host boundary. Preserve the user's turn when the sidecar is unavailable.
  process.stderr.write("Shared Brain Hook unavailable; use the active bootstrap/save instructions.\n");
  process.stdout.write("{}");
}
