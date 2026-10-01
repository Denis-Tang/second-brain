"""Host-neutral onboarding: the agent selects its host's supported lifecycle API."""
import json
from pathlib import Path


def build_prompt(vault_path: Path, mcp_config: dict) -> str:
    command = mcp_config["mcpServers"]["shared_brain"]
    hook = {**command, "args": command["args"][:-1] + ["hook"]}
    source_mode = "-m" in command["args"]
    installation = Path(__file__).resolve().parents[2] if source_mode else Path(command["command"]).parent
    runtime_note = "源码运行时先按 scripts/build.ps1 构建分发包，再使用现成 Hook 适配。" if source_mode else f"Hook 使用包内 Node.js：{installation / 'runtime' / 'node.exe'}，无需另装 Node.js。"
    return f"""请将 Shared Brain 接入当前 Agent 宿主，按真实宿主文档配置并验证。
Vault 绝对路径：{vault_path}
以上路径来自用户在 Shared Brain 中已保存的 Vault 设置；所有 Agent 复用同一个 Vault 和下面 MCP 配置中的 --home，不按宿主另建仓库。
本次仅接入当前 Agent。即使其他 Agent 已配置过，也不要重复初始化、重建 Obsidian 架构、复制模板或改写已有笔记；先检查当前宿主配置，已有相同 shared_brain 接入则复用，只补缺少的接入项。
MCP 配置（合并到现有配置，保留其他服务）：
```json
{json.dumps(mcp_config, ensure_ascii=False, indent=2)}
```
MCP 服务名统一为 shared_brain。
先读取宿主接入说明：{installation / 'integrations' / 'README.md'}。
已有适配优先使用，不另写脚本：Codex / Claude Code 使用 native-hooks.mjs，旧 Oh-DSH 使用 oh-dsh.mjs，DeepSeek Harness 0.1.7 使用 deepseek-harness.mjs；均在上述 integrations 目录。
{runtime_note}
以下基础目录由程序在保存 Vault 设置时自动补齐，不是让 Agent 再次创建的任务清单；已有架构和文件直接复用：
会话总结/    仅项目会话，保存为 <Agent>/YYYY/MM/日期-会话标识.md，同一会话持续更新
项目/        立项资料、项目知识；独立项目/独立知识/ 按对象保存公共电脑与工具变更
知识/        全局提示词.md，仅用户编辑，非空时所有会话全文加载
技能/        按项目目录与 独立项目/ 分组的 SKILL.md，须有完整步骤和实际成功结果
草稿/        按项目目录与 独立项目/ 建文件夹；用户在 Obsidian 编辑，需用时由用户提供
机器索引、维护进度、错误 JSON、图表数据由程序存应用专属数据目录。不要另建会话记录或日志目录。
不迁移旧个人资料，不复制宿主原生会话或完整工具输出，不访问或输出凭据。代码与交付物保留工作区，第二大脑只保存链接。

薄入口与执行规则：
1. 新根会话调用 bootstrap(cwd,session_id,agent,workspace_root,task=当前任务描述)，task 用不超过 500 字符的简短描述。session_id 必须稳定且取自宿主，不能每阶段随机生成；workspace_root 使用宿主工作区根目录。未提供时使用启动目录；临时 cd 不换工作区。任务未知可省略 task，明确后重新调用。非空 global_prompt 全文遵循；relevant 只返回少量相关知识/技能，其他按需 search。草稿不自动加载、检索或夜间整理。
2. 项目仅在应用的“项目配置”页面管理。bootstrap 按配置路径及子目录匹配，更具体目录优先；未配置路径直接作为独立会话，不询问、不自行创建或绑定项目。project_directory 是项目资料绝对目录，项目卡、任务和决策使用 save 写入；会话总结仍在会话总结目录，项目索引链接它们。配置变更后重新 bootstrap；保存时程序也重新核对归属。代码与交付物留在工作目录。
3. 按需 search。项目会话默认传 bootstrap 返回的项目 ID，只搜本项目；独立会话 project 留空，按相关性搜索公共池和全部项目。明确需要公共池时传 project="independent"，跨项目时传 project="all"。核对来源、适用条件与验证依据；涉及已知失败对象时可传 target/method/environment。不要为每个廉价动作强制检索。对象、方法、环境未知时不当成匹配；不把一页失败泛化为网站永久不可用。
4. 有用阶段由根代理 save，context 来自宿主，包含 role=root、session_id 与相等的 root_session_id；子代理只回传成果、失败和证据。项目会话 summary 传整份最新总结，保留必要历史阶段，项目进度当场更新；独立会话不写总结、项目卡、任务卡、决策或单独 memory。归档仅改状态，不移动文档。
5. 实际失败放 errors 数组，每项 target/method/symptom，建议提供 environment、attempts、workaround、impact=low或high。同一问题用同一 id 补充尝试。每会话一份 JSON。没有错误不建空报告；不要把 fail 字符串或预期探测未命中当成实际失败，不编造因果。
6. 错误立即可搜。后续检查实际效果成功后调用 feedback，关联原 session_id/error_id，提供 target/method/environment/evidence；换方法成功是绕行，不抹掉原方法失败。项目已验证知识可用 memory 当场保存并附 evidence。
7. 所有会话发生应用/工具安装、卸载、配置、skills、模型或电脑环境变更时，用 save(changes=[...]) 更新公共独立知识池的对应对象。每项 object/action/location/state 必填：对象、实际动作、具体文件/目录或设置范围、当前状态；evidence 记录真实验证结果，未验证留空，conditions 可注明适用条件。保留简短历史，后续撤销或迁移也更新，不写凭据值。普通源码修改只留项目记录。Hook 不自动观察电脑变更，由实际执行的根代理保存。
8. 收尾前项目会话保存完整 summary、progress/next_actions；所有会话核对变更及实际错误后传 errors_reviewed=true。独立会话可以只调用 save(context,errors_reviewed=true)，无事项不生成空文件。项目完成/暂停须遵用户决定。conflict 标记的结论仅在当前任务必须选择时询问。
9. 夜间只处理新增/变化与积压，按材料归属整理项目知识/技能或公共对象/技能，不跨项目合并。公共对象可以整理，但具体位置、简短变更历史和验证结果必须保留。全局提示词仅用户编辑；草稿不自动参加维护。生成技能须有实际成功过程，只保存知识库记录，不安装或执行。不会替用户改目标、装 Hook 或重试失败。默认不开付费维护；用户可在设置启用，官方 deepseek-flash 非思考，每月10元、每日3批/累计30,000输入/30,000输出，每批最多10,000输出。

Hook 程序入口（标准输入一个 JSON；stdout 一个 JSON；不调用模型）：
```json
{json.dumps(hook, ensure_ascii=False, indent=2)}
```
这是程序的宿主中立协议，不是可直接粘贴到任意宿主的 Hook 配置。已有适配的宿主按上述说明接入；仅其他宿主核对实际支持的事件和返回格式后写最小适配。若不支持对应事件，明确报告并保留主动执行的薄入口，不声称 Hook 生效。
事件必须映射真实根身份 role/session_id/root_session_id，不能把子代理标成 root：
- event=start：返回 additionalContext，注入启动 bootstrap 提醒。
- event=turn：每次新用户回合调用，重置本轮收尾检查；不能在工具调用或模型续答时重置。
- event=closeout：返回 decision=block/reason 时，按宿主协议提醒补写后继续；再次收尾返回空对象放行。同一回合只提醒一次。可附稳定 turn_id；禁止因 Hook 续答再次调用 turn。
- event=failure：可选，默认不装。仅宿主明确的实际工具失败才传 actual_failure=true 与精简 error 条目；预期探测传 expected_probe=true。此事件只记录，stdout 空对象，不向上下文注入失败提醒。不能把原始事件、完整命令、返回文本或凭据传给此入口。如会影响任务质量，则不启用，根代理主动记录即可。
失败 Hook 仅用于根代理；子代理失败由其结果回传。Hook 自身失败不得阻塞主任务，使用宿主支持的短超时；不要用模型完成 Hook 适配期间的错误推断。

配置时只增补必要薄入口和有文档支持的 Hook，仓库外配置先留 .bak，不覆盖其他约定。验证 status 与 bootstrap，并在临时会话检查项目匹配、未匹配独立会话、保存、一次收尾提醒；不要为了验证触发付费调用。报告实际配置位置、哪些 Hook 经过验证、关闭这些 Hook 的方法。
"""
