"""Host-neutral onboarding: the agent selects its host's supported lifecycle API."""
import json
from pathlib import Path


def build_prompt(vault_path: Path, mcp_config: dict) -> str:
    command = mcp_config["mcpServers"]["shared-brain"]
    hook = {**command, "args": command["args"][:-1] + ["hook"]}
    return f"""请将 Shared Brain 接入当前 Agent 宿主，按真实宿主文档配置并验证。
Vault 绝对路径：{vault_path}
MCP 配置（合并到现有配置，保留其他服务）：
```json
{json.dumps(mcp_config, ensure_ascii=False, indent=2)}
```
程序仅选定路径。请创建以下四个基础目录，保留已有文件：
会话总结/    实际保存为 <Agent>/YYYY/MM/日期-会话标识.md，同一会话持续更新同一份总结
项目/        每个项目只固定 项目.md、会话索引.md；任务/、决策/ 按需创建
知识/        普通知识和失败经验合并于此
技能/        有完整成功过程的可复用方法，按需读取 SKILL.md
机器索引、维护进度、错误 JSON、图表数据由程序存应用专属数据目录。不要另建会话记录或日志目录。
不迁移旧个人资料，不复制宿主原生会话或完整工具输出，不访问或输出凭据。代码与交付物保留工作区，第二大脑只保存链接。

薄入口与执行规则：
1. 新根会话调用 bootstrap(cwd,session_id,agent,workspace_root)。session_id 必须稳定且取自宿主，不能每阶段随机生成；workspace_root 使用宿主工作区根目录。程序否则按 Git 根/启动目录识别，临时 cd 不换项目。
2. needs_choice=true 时首次询问用户 new / existing / independent；再调用 bootstrap 传 choice，以及新项目 project_name 或已有 project ID。independent 对这个路径长期生效。移动/改名后新路径关联已有 project ID 一次，不凭目录名或 Git remote 合并。
3. 按需 search，并传 bootstrap 返回的 project；涉及已知失败对象时可传 target/method/environment。不要为每个廉价动作强制检索。对象、方法、环境未知时不当成匹配；不把一页失败泛化为网站永久不可用。
4. 有用阶段由根代理 save，context 来自宿主，包含 role=root、session_id 与相等的 root_session_id；子代理只回传成果、失败和证据。summary 传整份最新会话总结，保留必要历史阶段；独立会话也保存总结，不强制任务卡。项目进度当场更新，归档仅改状态，不移动文档。
5. 实际失败放 errors 数组，每项 target/method/symptom，建议提供 environment、attempts、workaround、impact=low或high。同一问题用同一 id 补充尝试。每会话一份 JSON。没有错误不建空报告；不要把 fail 字符串或预期探测未命中当成实际失败，不编造因果。
6. 错误立即可搜。后续检查实际效果成功后调用 feedback，关联原 session_id/error_id，提供 target/method/environment/evidence；换方法成功是绕行，不抹掉原方法失败。已验证知识可用 memory 当场保存并附 evidence。
7. 收尾前保存完整 summary、项目 progress/next_actions，核对错误报告后传 errors_reviewed=true。项目完成/暂停须遵用户决定。conflict 标记的结论仅在当前任务必须选择时询问。
8. 夜间只处理新增/变化与积压，来源与历史保留；生成技能需要实际成功过程。不会替用户改目标、执行技能、装 Hook 或重试失败。默认不开付费维护；用户可在设置启用，官方 deepseek-flash 非思考，每月10元、每日3批/累计30,000输入/4,000输出。

Hook 程序入口（标准输入一个 JSON；stdout 一个 JSON；不调用模型）：
```json
{json.dumps(hook, ensure_ascii=False, indent=2)}
```
这是程序的宿主中立协议，不是可直接粘贴到任意宿主的 Hook 配置。请先核对当前宿主实际支持的事件和返回格式，再写最小适配；若不支持对应事件，明确报告并保留主动执行的薄入口，不声称 Hook 生效。
事件必须映射真实根身份 role/session_id/root_session_id，不能把子代理标成 root：
- event=start：返回 additionalContext，注入启动 bootstrap 提醒。
- event=turn：每次新用户回合调用，重置本轮收尾检查；不能在工具调用或模型续答时重置。
- event=closeout：返回 decision=block/reason 时，按宿主协议提醒补写后继续；再次收尾返回空对象放行。同一回合只提醒一次。可附稳定 turn_id；禁止因 Hook 续答再次调用 turn。
- event=failure：可选，默认不装。仅宿主明确的实际工具失败才传 actual_failure=true 与精简 error 条目；预期探测传 expected_probe=true。此事件只记录，stdout 空对象，不向上下文注入失败提醒。不能把原始事件、完整命令、返回文本或凭据传给此入口。如会影响任务质量，则不启用，根代理主动记录即可。
失败 Hook 仅用于根代理；子代理失败由其结果回传。Hook 自身失败不得阻塞主任务，使用宿主支持的短超时；不要用模型完成 Hook 适配期间的错误推断。

配置时只增补必要薄入口和有文档支持的 Hook，仓库外配置先留 .bak，不覆盖其他约定。验证 status 与 bootstrap，并在临时会话检查首次询问、保存、一次收尾提醒；不要为了验证触发付费调用。报告实际配置位置、哪些 Hook 经过验证、关闭这些 Hook 的方法。
"""
