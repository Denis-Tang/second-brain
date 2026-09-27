# Shared Brain

面向干净 Obsidian 仓库的本地第二大脑。Windows 桌面只保留设置、总观两页；Agent 通过 MCP 启动、检索、保存和反馈。无需复制旧 DSH，也不依赖开发者电脑路径。

## 开始使用

从 [GitHub Releases](https://github.com/Denis-Tang/second-brain/releases/latest) 下载 `shared-brain-0.4.0-windows-x64.zip`，完整解压后运行 `shared-brain/shared-brain.exe`，保留同目录 `_internal` 和 `integrations`。Windows x64 需要 WebView2 Runtime；分发包已包含 Python，无需另装 Python。

首次在设置选择私人知识库路径，再复制内置提示词给 Agent。程序仅保存路径，由 Agent 创建目录、合并 MCP 与薄入口、按实际宿主支持配置 Hook。知识库应放在源码目录之外。

本仓库发布 Shared Brain 程序源码；私人知识库、应用状态、宿主配置、`build` 和 `.venv` 不随源码或下载包发布。Windows 可运行包通过 Releases 提供，不放入 Git 历史。

```text
会话总结/<Agent>/YYYY/MM/日期-会话标识.md
项目/<名称>/项目.md
项目/<名称>/会话索引.md
项目/<名称>/任务/      # 有需要才建
项目/<名称>/决策/      # 有需要才建
知识/                 # 包含正常经验与失败经验
技能/<标识>/SKILL.md
资料/                 # 服务端导入时才建，界面没有导入入口
```

同一会话更新同一份总结；项目只保存相对链接。工作区内新会话关联同一项目。首次遇到未知路径，由 Agent 询问新项目、关联已有项目或独立会话；独立选择长期记住。独立会话仍写总结，不强制项目或任务卡。项目进度即时保存，完成/暂停只改状态，不物理搬迁。代码和交付物留原工作区。

工作区优先取宿主提供的根目录，否则取 Git 根或启动目录；临时 `cd` 不改会话绑定。项目 ID 写在项目 Markdown 属性里，路径映射在应用数据目录。改名或移动后，只需将新路径关联到原项目一次；不凭名称或 Git remote 自动合并。

## 存储与安装

Windows 应用数据默认 `%LOCALAPPDATA%/SharedBrain`；可用 `SHARED_BRAIN_HOME` 或命令开头的 `--home PATH` 指定。MCP 与桌面须使用同一 home。SQLite 索引、工作区映射、维护处理进度、图表历史、费用账本以及 `errors/<仓库标识>/<会话标识>.json` 均在这里，不进 Obsidian。API key 存操作系统凭据库，不回显。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
shared-brain init ./example-vault
shared-brain
```

Python 3.11+；Windows 桌面依赖系统 WebView2。分发包内含 Python。项目没有自动迁移旧版 `Shared Brain/Tasks` 等目录；升级请连接干净仓库并重新复制接入提示词，原资料保留。

## MCP 工作流

`shared-brain config-mcp` 输出本次安装的可执行路径与参数；宿主启动 `shared-brain mcp`。提示词也包含相同配置，不包含密钥。

| 工具 | 用法 |
| --- | --- |
| `bootstrap` | 必填 cwd、宿主稳定 session_id、agent。可传 workspace_root；未知路径返回 needs_choice 和已有项目，确认后传 choice=new/existing/independent。new 配 project_name，existing 配 project ID。 |
| `search` | query、project（bootstrap 返回的 ID），可附 target/method/environment。返回相关笔记及最多3条有条件错误线索，不调用模型。 |
| `save` | context 含宿主 role=root、session_id、root_session_id；summary 是完整最新总结。goal/progress/next_actions 即时更新项目，task_id/decision 按需。errors 单独保存；收尾核对后传 errors_reviewed=true。 |
| `feedback` | 根代理在实际成功并读回效果后提交 error_session_id、error_id、observation（target/method/environment/evidence）。 |
| `status` | 就绪、待处理量、统计与本月费用/预留；不调用模型。 |

只保存有用阶段，不记录每次工具调用。子代理向根代理回传结果与失败，不独立保存共享记录。`memory={title,body,verified,evidence,conditions}` 可当场保存明确验证的经验；verified=true 需要实际结果依据。

错误项包含 target、method、symptom，可补 environment（文本键值表）、attempts、workaround、impact=low/high、id。同一会话同一对象/方法/条件默认合并，也可沿用 id 补充尝试；每会话一份 JSON，无错不建空报告。仅记真实失败，不按 fail 字符串推断因果。URL 去掉凭据、query 与 fragment；影响适用性的非敏感 query 参数应写入 environment。不得提交完整工具输出、命令或秘密。

失败保存后即可检索。检索优先按项目与显式对象/方法/环境过滤，再按关键词和时间排序；未知条件要由 Agent 核对。bootstrap 只带强相关高影响提醒，最多2条约120估算 tokens；search 错误部分最多3条约300估算 tokens，放不下完整适用条件则省略，而非截断条件。原报告可按返回路径读取。

同一对象/方法且已知环境完全一致的成功，撤下当前失败提醒但保留历史；不同方法成功增加绕行；新环境或未知条件保留不同观察。依赖原错误的知识暂时退出当前检索，待维护补充结果。冲突记录带 conflict=true，仅当前任务需要选择时询问用户。

## Hook 接入

当前提供通用 MCP 协议和 DeepSeek Harness 0.1.7 生命周期适配。Codex、Claude Code、旧 Oh-DSH 的专用适配和真实会话接力尚未完成；不要直接把 Harness 插件用于旧 Oh-DSH。

内置提示词给出真实程序位置的 `shared-brain --home PATH hook` 命令。它从 stdin 接收一个 JSON 事件并输出一个 JSON 对象；这是宿主中立协议，需要接入 Agent 按宿主文档映射事件/返回格式，不是通用宿主配置文件。程序不擅自安装 Hook。

每个事件包含宿主真实 role、session_id、root_session_id；子代理事件忽略。事件约定：

- `start`：返回 additionalContext，提醒调用 bootstrap。
- `turn`：每个新用户回合重置收尾状态；模型续答/工具调用不能重置。
- `closeout`：漏保存/漏核对时返回 decision=block 与 reason，提醒补写后再结束；同一回合只提醒一次。宿主需把此结果映射为原生继续机制。支持可选稳定 turn_id。
- `failure`：可选，默认不装。actual_failure=true、error 为精简错误项，只落盘并返回空对象。expected_probe=true 忽略。无模型调用、无重试指导、无上下文注入。

已实现和测试的是上述程序端协议。具体宿主必须确认支持启动、新用户回合、可继续的收尾事件后再安装；不支持时保留主动薄入口，并明确未启用的 Hook。失败 Hook 若影响任务质量则不装。可通过移除接入 Agent 添加的 Hook 配置关闭。

## DeepSeek Harness 原生接入

`integrations/deepseek-harness.mjs` 提供 Harness 0.1.7 的原生生命周期适配。它通过实际运行时 agents.roots() 区分根与子代理，以 source.kind=user 识别新用户输入，补写消息不重置收尾提醒。只传会话身份和事件，不读取原生会话或工具输出，不安装失败 Hook。

在 Harness 当前 profile 的 cordis.patch.yml 添加两项 insert：MCP 使用 `@deepseek-ai/dsh-mcp-client`，serverName 为 `shared_brain`，transport 为 stdio，command 为当前 `shared-brain.exe`，args 为 `--home <应用数据目录> mcp`。生命周期插件 name 为 sidecar 的 file URL，config 包含同一 command 和 home。保留已有 profile 项，修改前留 `.bak`。

薄入口可放 `$DSH_HOME/AGENTS.md`。仓库位置通过 Shared Brain 设置选择，适配插件不写死用户路径。

关闭接入时在 profile 中移除 `shared-brain-mcp` 和 `shared-brain-lifecycle` 两个新增项，再移除薄入口中对应规则；不需要删除仓库资料。更换程序安装位置时更新 command 和 sidecar URL。配置支持热加载，建议新开会话验证。真实模型对 Hook 的遵循仍需实际会话确认；本地测试不产生模型费用。

## 夜间维护与费用

默认关闭；用户在设置启用后，应用运行期间每天本机时间02:00处理增量和积压。关闭窗口仍在托盘，退出则停止；错过时点后下次启动可补处理。每日批次额度与费用记录跨重启保留。`shared-brain maintain` 手动处理也共用额度。

费用核算目前支持官方 `https://api.deepseek.com` 的 `deepseek-flash`（V4.1 Flash），明确关闭思考。月上限人民币10元；北京时间每日最多3个请求，累计输入30,000、输出4,000 tokens。每批最多4项，超长资料分段处理，未处理尾部保留。无新增无请求；同批重复内容本地去重，只有相关旧知识进入输入。

调用前按UTF-8字节上界加消息开销计算输入预留，并按高峰价格预留费用；有可信 usage 后按实际输入、缓存命中与输出结算。已计费但内容格式错误的响应仍结算；网络失败或缺 usage 保留最大预留，不自动重试。设置页显示已用/预留与剩余费用。预算不足停止新请求，保存/search不受影响。

单价依据2026-09-27官方文档：空闲时段每百万输入1元、缓存输入0.02元、输出4元，高峰两倍；节假日按普通工作日保守核算。价格变化需更新费用核算；账本仅限制本应用维护，不包含其他客户端或手动连接测试。测试连接会调用配置端点。

维护可新增来源明确的知识、追加兼容条件/修正、合并重复知识，或从完整实际成功过程整理技能。合并后的旧条目保留正文和 merged_into 指针，退出当前检索，不删除原文；冲突保留来源待实际任务询问。生成内容不因模型整理自动变成已验证。模型不会改项目目标/完成状态、偏好或规则，不安装 Hook，不执行技能或重新尝试错误操作。

## 界面与验证

总观显示会话总结数、知识数（含技能/草稿）和正文估算 token 的真实增长历史；统一切换24小时、7天、30天。正文规模不等于API用量，费用在设置页单独显示。

```powershell
python -m pytest
python -m pip install -e ".[build]"
pwsh -NoProfile -File scripts/build.ps1
```

构建输出在 `dist/shared-brain`。Harness 适配测试还需要 Node.js 和已构建的 Windows EXE；缺少时该项会跳过。

0.4.0 的本地测试覆盖工作区关联/搬迁、独立会话、总结与项目索引、结构化错误与成功反馈、Hook一次提醒、MCP工具、增量维护/技能生成及预算。模型测试使用模拟HTTP响应，不证明真实提炼质量；真实供应商质量、收费及具体宿主Hook需启用后验证。

未提交个人资料或凭据；不自动迁移旧知识库。保留服务端 `import_document`（UTF-8 Markdown/TXT），不恢复导入界面。MIT License。
