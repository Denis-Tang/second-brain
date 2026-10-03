// Native lifecycle adapter for DeepSeek Harness 0.1.7.
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";

export const name = "shared-brain-lifecycle";
export const inject = ["agents"];

function message(text) {
  return Object.freeze({ id: randomUUID(), role: "user", source: { kind: "shared-brain" },
    content: [{ type: "text", text }] });
}

export function apply(ctx, config) {
  const isRoot = (agent) => ctx.agents.roots().includes(agent);
  const identity = (agent) => ({ role: "root", session_id: agent.session.header.id,
    root_session_id: agent.session.header.id });
  const lastInput = new WeakMap();
  const hook = (agent, event) => new Promise((resolve) => {
    const child = execFile(config.command, ["--home", config.home, "hook"],
      { windowsHide: true, timeout: 2000, maxBuffer: 16384, encoding: "utf8" }, (error, stdout) => {
        if (error) { ctx.logger.warn("Shared Brain Hook unavailable; continue with active save."); return resolve({}); }
        try { resolve(JSON.parse(stdout)); }
        catch { ctx.logger.warn("Shared Brain Hook returned invalid JSON; continue with active save."); resolve({}); }
      });
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify({ ...identity(agent), event }));
  });
  ctx.on("agent/created", async ({ agent }) => {
    if (!isRoot(agent)) return;
    const args = { cwd: agent.session.header.cwd, session_id: agent.session.header.id, agent: "DeepSeek-Harness" };
    const result = await hook(agent, "start");
    agent.inject(message(`${result.additionalContext || "请先调用 Shared Brain bootstrap。"}
当前宿主根身份 context=${JSON.stringify(identity(agent))}。
调用 mcp__shared_brain__bootstrap，参数 ${JSON.stringify(args)}。项目由应用的项目配置决定，未匹配路径直接作为独立会话；项目资料目录见 project_directory，不询问或自行创建项目。
任务明确后带 task=当前任务描述重新 bootstrap，描述不超过500字符；遵循全文 global_prompt，参考少量 relevant，其他按需 search。项目默认传项目 ID，独立会话留空搜全部；project="independent" 只搜公共池，"all" 搜全部。核对来源与条件。草稿只由用户提供，不自动检索或整理。
项目会话 save 完整 summary 与项目进度；独立会话也 save 完整 summary，但不创建项目、任务卡、决策或单独 memory。所有会话发生安装/卸载、工具配置、skills、模型、PATH/代理等环境变更时，save changes 数组，每项 object/action/location/state，附实际 evidence/conditions，更新公共对象状态与历史；普通源码修改留在项目记录。
收尾核对变更与实际错误后 errors_reviewed=true；独立会话同样 save(context,summary=完整总结,errors_reviewed=true)，无变更或错误不生成空对象或错误报告。全局提示词仅用户编辑，生成技能不自动安装执行；子代理只回传。`));
  });
  ctx.on("agent/pre-step", async ({ agent, messages }, next) => {
    if (isRoot(agent)) {
      const user = messages.filter((item) => item.source.kind === "user").at(-1);
      if (user && lastInput.get(agent) !== user.id) {
        lastInput.set(agent, user.id);
        await hook(agent, "turn");
      }
    }
    return next();
  });
  ctx.on("agent/turn-stopping", async ({ agent }) => {
    if (!isRoot(agent)) return;
    const result = await hook(agent, "closeout");
    if (result.decision === "block") agent.steer(message(result.reason));
  });
}
