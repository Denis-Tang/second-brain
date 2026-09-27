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
调用 mcp__shared_brain__bootstrap，参数 ${JSON.stringify(args)}。未知路径先询问 new/existing/independent，不自行建项目。
用本次 context 保存有用阶段，子代理只回传；收尾核对独立 errors 并 save 完整 summary、项目进度，errors_reviewed=true。`));
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
