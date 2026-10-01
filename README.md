# Shared Brain

面向干净 Obsidian 仓库的本地第二大脑。Windows 桌面提供总观、项目配置、设置三页；Agent 通过 MCP 启动、检索、保存和反馈。无需复制旧 DSH，也不依赖开发者电脑路径。

当前源码与本地构建为 **0.4.1**，尚未发布到 Releases。以下操作对应 0.4.1；公开下载仍为 0.4.0，请按其 Release 说明操作。

## 开始使用

完整解压 `shared-brain-0.4.1-windows-x64.zip` 后运行 `shared-brain/shared-brain.exe`，保留同目录 `_internal`、`integrations` 和 `runtime`。Windows x64 需要 WebView2 Runtime；分发包包含 Python 与 Node.js，无需另装。已发布版本见 [GitHub Releases](https://github.com/Denis-Tang/second-brain/releases/latest)。

首次在设置选择私人知识库路径，点击“保存设置”统一保存路径和维护配置，再复制内置提示词给 Agent。程序自动创建知识库基础目录；项目归属在“项目配置”页面管理，由 Agent 合并 MCP 与薄入口、按实际宿主支持配置 Hook。知识库应放在源码目录之外。

本仓库发布 Shared Brain 程序源码；私人知识库、应用状态、宿主配置、`build` 和 `.venv` 不随源码或下载包发布。Windows 可运行包通过 Releases 提供，不放入 Git 历史。

```text
会话总结/<Agent>/YYYY/MM/日期-会话标识.md   # 仅项目会话
项目/<名称>/项目.md
项目/<名称>/会话索引.md
项目/<名称>/任务/      # 有需要才建
项目/<名称>/决策/      # 有需要才建
项目/<名称>/知识/      # 项目经验
项目/独立项目/独立知识/ # 公共电脑与工具对象记录、整理经验
知识/全局提示词.md     # 用户编辑，所有会话非空必载
技能/<项目目录名或独立项目>/<标识>/SKILL.md
草稿/<项目目录名或独立项目>/ # 仅存放，由用户在 Obsidian 管理并手动提供给 Agent
```

同一项目会话更新同一份总结；项目只保存相对链接。程序按显式配置的工作目录及子目录识别项目，更具体目录优先；未匹配直接独立。独立会话不写总结、项目卡、任务或决策，只在发生指定电脑与工具变更时更新公共对象；真实失败仍独立保存，无错不建文件。项目进度即时更新 `progress` 属性，保留原正文；bootstrap 分别返回 `progress` 和 `notes`。完成/暂停只改状态。代码和交付物留原工作区。

总观页下方可单独编辑全局提示词，直接保存到 `知识/全局提示词.md`；不存配置副本，不向 MCP 暴露编辑工具。旧 `偏好.md` 在读取时并入该文件后删除。正文非空时 bootstrap 全文返回，维护不处理。知识库初始化创建 `草稿/独立项目/`，为已有及新建项目补建对应草稿目录；改显示名称不搬目录，删除立项保留草稿。草稿不索引、不搜索、不统计、不注入、不参加维护。

工作区优先取宿主提供的根目录，否则使用启动目录；不再根据 Git 根目录提升路径，临时 `cd` 不改会话工作区。项目 ID 写在项目 Markdown 属性里，路径配置在应用数据目录，按知识库隔离。每次 bootstrap 和保存都会重新匹配当前配置，不沿用旧项目缓存。所有宿主需使用相同 home 和知识库。

## 项目配置

“项目配置”按创建时间从新到旧展示全部项目和数量，编辑或改名不改变顺序；右上角“新增项目”打开独立创建窗口。填写名称并通过“添加文件夹”选择一个或多个已有工作目录；“创建项目”保存后返回列表，可再次点击“新增项目”添加其他项目。每个项目的“编辑”只修改该项目，与新增入口分开。保存后自动创建 `项目/<名称>/项目.md` 和 `会话索引.md`；任务、决策按需创建。一个目录只能配置给一个项目；父子目录配置到不同项目时，子目录及其后代归更具体的项目。支持复制项目名称；“复制路径”仅放在具体工作目录后，每次只复制该条路径；不自动创建各 Agent 宿主的工作区。

列表包含原有项目，可直接编辑并补充路径。允许移除全部路径，项目卡与历史总结保留，项目不再参与匹配。改名仅更新显示名称，保留 ID、原资料目录和已有链接。点击“删除项目”先弹出确认框，仅“确认删除”执行删除；取消或按 Esc 不作修改。删除移除立项和路径绑定，将项目卡原样转存为同目录 `立项归档.md`；会话索引、总结、任务、决策、用户原始资料和工作目录保留，不再参与原项目归属。工作目录搬迁后在此页修改路径；同名目录与 Git remote 不参与判定。

bootstrap 返回 `project_directory`（项目资料绝对路径），独立会话为空。Agent 使用 save 更新项目卡、任务、决策与总结索引；项目经验放在项目的 `知识/`。所有会话发生安装、卸载、工具配置、skills、模型及电脑环境变更时，通过 `changes` 写公共独立知识池；普通源码修改留在项目记录。对象用稳定名称标识，保存实际位置、最新状态、验证和变更历史。配置变更后重新 bootstrap；save 和失败 Hook 也重新匹配。旧知识、技能、资料与历史总结不迁移或改写；旧独立总结、平铺经验和导入资料不进入新版维护队列。

升级后旧的 Agent 路径绑定不再生效；请在项目配置页为原项目添加目录，重新复制接入提示词并更新宿主 Hook 适配文件。MCP 已移除 choice、project、project_name 启动参数和 needs_choice 返回字段，重启 MCP 连接以刷新工具定义。

## 存储与安装

应用数据默认 `~/.shared-brain`（Windows 为 `%USERPROFILE%/.shared-brain`），避开打包宿主的 AppData 私有重定向；可用 `SHARED_BRAIN_HOME` 或命令开头的 `--home PATH` 指定。所有宿主的 MCP、Hook 与桌面须使用同一 home。已有安装迁移时保留工作区映射、错误报告和费用账本；`settings.json` 的 `credential_account` 可保留原凭据账号引用，无需读取或搬运密钥。SQLite 索引、工作区映射、维护处理进度、图表历史、费用账本以及 `errors/<仓库标识>/<会话标识>.json` 均在这里，不进 Obsidian。API key 存操作系统凭据库，不回显。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
shared-brain init ./example-vault
shared-brain
```

Python 3.11+；Windows 桌面依赖系统 WebView2。分发包内含 Python。项目没有自动迁移旧版 `Shared Brain/Tasks` 等目录；升级请连接干净仓库并重新复制接入提示词，原资料保留。

## MCP 工作流

`shared-brain config-mcp` 输出本次安装的可执行路径与参数，MCP 服务名统一为 `shared_brain`；宿主启动 `shared-brain mcp`。提示词也包含相同配置，不包含密钥。

| 工具 | 用法 |
| --- | --- |
| `bootstrap` | 必填 cwd、稳定 session_id、agent。可传 workspace_root、task_id、task（最多500字符）；返回非空 global_prompt 全文、项目上下文及最多3条与当前任务相关的知识/技能。 |
| `search` | query，project=项目 ID 仅本项目，independent 仅公共池，空值/all 搜全部。独立会话默认全部，项目不默认搜公共池。返回来源、条件、依据和最多3条错误线索；不调用模型。 |
| `save` | 根身份 context；项目 summary 是完整最新总结，goal/progress/next_actions 更新项目。changes 含 object/action/location/state，可附 evidence/conditions；所有会话写公共对象。errors 单独保存；收尾核对后 errors_reviewed=true。 |
| `feedback` | 根代理在实际成功并读回效果后提交 error_session_id、error_id、observation（target/method/environment/evidence）。 |
| `status` | 就绪、待处理量、统计与本月费用/预留；不调用模型。 |

只保存有用阶段，不记录每次工具调用。子代理向根代理回传。项目的 `memory={title,body,verified,evidence,conditions}` 可当场保存经验；verified=true 需要实际结果依据。独立会话通过 changes 保存指定变更，不另写普通经验或总结；无事项时收尾不用生成文档。

错误项包含 target、method、symptom，可补 environment（文本键值表）、attempts、workaround、impact=low/high、id。同一会话同一对象/方法/条件默认合并，也可沿用 id 补充尝试；每会话一份 JSON，无错不建空报告。仅记真实失败，不按 fail 字符串推断因果。URL 去掉凭据、query 与 fragment；影响适用性的非敏感 query 参数应写入 environment。不得提交完整工具输出、命令或秘密。

失败保存后即可检索。检索优先按项目与显式对象/方法/环境过滤，再按关键词和时间排序；未知条件要由 Agent 核对。知识与错误共用切词：中文连续段按二字滑窗，单字保留，中英混合保留英文词，去重后最多512词，排除“怎么、然后、一直”；中文查询至少3个有效词时须命中2个，显式指定错误对象仍按对象匹配。中文按命中词数排序，纯英文长词保留FTS，短词保留子串匹配。bootstrap 只带强相关高影响提醒，最多2条约120估算 tokens；search 错误部分最多3条约300估算 tokens，放不下完整适用条件则省略，而非截断条件。原报告可按返回路径读取。

同一对象/方法且已知环境完全一致的成功，撤下当前失败提醒但保留历史；不同方法成功增加绕行；新环境或未知条件保留不同观察。依赖原错误的知识暂时退出当前检索，待维护补充结果。维护在额度内将同归属的待复核知识/技能提供为旧结论，即使沿用原标题也须等报告分组全部处理完才解除隐藏；已合并条目继续隐藏，解除标记不会使已处理条目重新排队，未处理完的正文进度保留。冲突记录带 conflict=true，仅当前任务需要选择时询问用户。

## Hook 接入

0.4.1 分发包提供 Codex、Claude Code、旧 Oh-DSH 和 DeepSeek Harness 的生命周期适配，并包含 [宿主接入](integrations/README.md) 说明。内置提示词指向这些现成脚本；Codex / Claude 命令 Hook 使用包内 `runtime/node.exe`。已发布的 v0.4.0 ZIP 只包含 DeepSeek Harness 适配。不要直接把 Harness 插件用于旧 Oh-DSH。

内置提示词给出真实程序位置的 `shared-brain --home PATH hook` 命令。它从 stdin 接收一个 JSON 事件并输出一个 JSON 对象；已有适配按随包说明接入，其他宿主才需要按其文档映射事件/返回格式。程序不擅自安装 Hook。

每个事件包含宿主真实 role、session_id、root_session_id；子代理事件忽略。事件约定：

- `start`：返回 additionalContext，提醒调用 bootstrap。
- `turn`：每个新用户回合重置收尾状态；模型续答/工具调用不能重置。
- `closeout`：项目漏总结或已知实际错误未核对时提醒一次；独立会话核对指定变更与错误，不要求总结，普通问答直接结束。支持可选稳定 turn_id。Hook 不读取操作内容，实际变更由执行 Agent 主动保存。
- `failure`：可选，默认不装。actual_failure=true、error 为精简错误项，只落盘并返回空对象。expected_probe=true 忽略。无模型调用、无重试指导、无上下文注入。

已实现和测试的是上述程序端协议。具体宿主必须确认支持启动、新用户回合、可继续的收尾事件后再安装；不支持时保留主动薄入口，并明确未启用的 Hook。失败 Hook 若影响任务质量则不装。可通过移除接入 Agent 添加的 Hook 配置关闭。

## DeepSeek Harness 原生接入

`integrations/deepseek-harness.mjs` 提供 Harness 0.1.7 的原生生命周期适配。它通过实际运行时 agents.roots() 区分根与子代理，以 source.kind=user 识别新用户输入，补写消息不重置收尾提醒。只传会话身份和事件，不读取原生会话或工具输出，不安装失败 Hook。

在 Harness 当前 profile 的 cordis.patch.yml 添加两项 insert：MCP 使用 `@deepseek-ai/dsh-mcp-client`，serverName 为 `shared_brain`，transport 为 stdio，command 为当前 `shared-brain.exe`，args 为 `--home <应用数据目录> mcp`。生命周期插件 name 为 sidecar 的 file URL，config 包含同一 command 和 home。保留已有 profile 项，修改前留 `.bak`。

薄入口可放 `$DSH_HOME/AGENTS.md`。仓库位置通过 Shared Brain 设置选择，适配插件不写死用户路径。

关闭接入时在 profile 中移除 `shared-brain-mcp` 和 `shared-brain-lifecycle` 两个新增项，再移除薄入口中对应规则；不需要删除仓库资料。更换程序安装位置时更新 command 和 sidecar URL。配置支持热加载，建议新开会话验证。真实模型对 Hook 的遵循仍需实际会话确认；本地测试不产生模型费用。

## 夜间维护与费用

默认关闭；用户在设置启用后，应用运行期间每天本机时间02:00处理增量和积压。Windows 桌面只运行一个实例，不同安装目录或 home 再次启动都显示已有窗口，也能识别未使用单实例机制的旧版桌面；关闭窗口仍在托盘，只有右键托盘选择“退出”才结束应用。MCP 和 Hook 不受桌面单实例限制。错过时点后下次启动可补处理。每日批次额度与费用记录跨重启保留。`shared-brain maintain` 手动处理也共用额度。

费用核算目前支持官方 `https://api.deepseek.com` 的 `deepseek-flash`（V4.1 Flash），明确关闭思考。月上限人民币10元；北京时间每日最多3个请求，累计输入30,000、输出30,000 tokens，每批最多输出10,000 tokens。每批最多4项，超长资料分段处理，未处理尾部保留。无新增无请求；同批重复内容本地去重，只有相关旧知识进入输入。

项目总结按正文版本保留提炼进度，处理中途保存不会重头开始；先完成正在处理的版本，再按行提交新旧正文的增删改及前后两行上下文。正文未变时只同步处理标记，不调用模型；失败不推进进度。旧进度没有正文快照时，文件未变可沿用偏移，已改变的旧版本无法还原，首次按当前全文建立基线。

调用前按UTF-8字节上界加消息开销计算输入预留，并按高峰价格预留费用；有可信 usage 后按实际输入、缓存命中与输出结算。已计费但内容格式错误的响应仍结算；网络失败或缺 usage 保留最大预留，不自动重试。设置页显示已用/预留与剩余费用。预算不足停止新请求，保存/search不受影响。

单价依据2026-09-27官方文档：空闲时段每百万输入1元、缓存输入0.02元、输出4元，高峰两倍；节假日按普通工作日保守核算。价格变化需更新费用核算；账本仅限制本应用维护，不包含其他客户端或手动连接测试。测试连接会调用配置端点。

维护在同一材料归属内补充知识、合并重复经验、从完整实际成功过程生成技能；项目材料产出到本项目，公共材料产出到独立项目。技能保存成功依据及来源，不自动安装或执行。对象历史分段处理，每段同时提供最新事实与已有整理经验；未处理尾部跨维护轮保留。整理仅更新“整理经验”，具体位置、当前状态、真实变更历史及验证保留。合并条目保留正文和 merged_into，不删除原文；生成内容不自动成为已验证。全局提示词、草稿与旧导入资料不参加维护。

更新同名知识或技能时，模型须基于 existing 中的完整旧正文返回整理后的完整新版，替换生成正文并累计保留来源，不再逐次追加。若旧正文未检索到或超出本批额度，同名覆盖会报错并保留原文及未完成的材料；需处理检索或篇幅问题后再维护，不自动重试。总结差异中的删除表示原文变化，不直接视为旧结论已被证伪。

## 界面与验证

英文使用随包附带的 Noto Serif（SIL OFL 1.1）；中文标题使用系统黑体，其余中文使用系统幼圆。

总观显示项目会话总结数、知识与技能（含公共对象）数量及正文估算 token 的真实增长历史；草稿和全局提示词不计入。统一切换24小时、7天、30天；正文规模不等于API用量，费用在设置页单独显示。

```powershell
python -m pytest
python -m pip install -e ".[build]"
pwsh -NoProfile -File scripts/build.ps1
```

构建机器需要 Python 与 Node.js；脚本将当前 Node.js 运行文件及同版本许可声明放入分发包。默认输出在 `dist/shared-brain`，可用 `scripts/build.ps1 -OutputDirectory dist/v0.4.1` 构建到独立目录，避免覆盖仍在运行的程序。应用标识、窗口、托盘及 EXE 统一使用 `web/icon.svg` 及其 PNG/ICO 版本，四角透明；ICO 包含 16–256 像素的 9 种尺寸。Windows EXE 使用无控制台模式，桌面启动不创建命令行窗口；同一 EXE 恢复宿主传入的标准输入输出管道，继续支持 MCP、Hook 和命令行调用，Hook 子进程也使用 windowsHide。宿主适配测试使用已构建包内的 Node.js、脚本和 EXE；未构建时该项会跳过。

0.4.1 的本地测试覆盖显式项目多路径配置/搬迁、嵌套目录归属、独立会话、总结与项目索引、结构化错误与成功反馈、Hook一次提醒、MCP工具、增量维护/技能生成及预算；四种宿主适配在清空 PATH 后使用包内 Node.js 验证。模型测试使用模拟HTTP响应，不证明真实提炼质量；真实供应商质量、收费及具体宿主Hook需启用后验证。

测试使用隔离知识库和模拟模型，不替代真实宿主遵循验收。可用 `SHARED_BRAIN_TEST_EXE` 指定待测构建的 EXE；未指定时集成测试使用 `dist/shared-brain/shared-brain.exe`。不自动迁移旧资料；旧导入接口不创建草稿，也不进入新版维护。MIT License。
