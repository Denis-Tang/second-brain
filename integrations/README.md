# 宿主接入

所有宿主连接同一 `shared-brain.exe --home <应用数据目录> mcp`，服务名统一为 `shared_brain`。知识库路径由程序设置管理，适配器不包含个人路径或凭据。宿主规则只保留启动、按需检索、有用阶段保存、实际错误与成功反馈；模型、权限提示和其他插件独立保留。

任务明确后，根代理在 `bootstrap` 中传入不超过 500 字符的简短 `task`；非空 `global_prompt` 全文加载，只由用户编辑。`relevant` 按当前任务返回少量知识和技能。项目会话检索默认传项目 ID，只搜本项目；独立会话留空搜索公共独立池与全部项目。按需传 `project="independent"` 仅搜公共池，或 `project="all"` 搜全部，并核对结果的来源、适用条件与验证依据。`草稿/` 仅由程序建立项目及独立项目目录，用户在 Obsidian 编辑，需要时手动提供；不自动加载、搜索或夜间整理。

项目会话继续保存完整总结和项目进度；独立会话不保存总结、项目卡、任务卡、决策或单独经验。所有会话发生安装、卸载、工具配置、skills、模型及电脑环境变更后，通过 `save(changes=[...])` 更新公共对象文件，保留当前状态和简短变更历史。每项包含 `object`、`action`、`location`、`state`，`evidence` 写实际验证结果，未验证可空；`conditions` 可注明适用条件。具体位置应说明实际文件、目录或用户级/系统级设置范围，凭据值不写入。普通源码修改留在项目记录。Hook 不观察工具输出或电脑变更，记录由执行 Agent 主动保存。

实际错误仍单独保留并可成功反馈。收尾核对变更和错误后传 `errors_reviewed=true`；无事项的独立会话可以只调用 `save(context,errors_reviewed=true)`，不生成空文件。维护只按材料归属生成知识或技能，公共对象材料的技能归 `技能/独立项目/`；全局提示词和草稿不参加维护，技能仅作记录，不自动安装或执行。

## Codex / Claude Code

0.4.1 分发包内置 Node.js，无需另装。将 MCP 以 `shared_brain` 注册到 Codex 的用户级 `config.toml` 或 Claude 的用户级 MCP 配置。配置必须显式带 `--home`，不要依赖 MCP 子进程继承环境变量。

命令 Hook 调用：

```text
"<安装目录>/runtime/node.exe" "<安装目录>/integrations/native-hooks.mjs" Codex "<安装目录>/shared-brain.exe" "<应用数据目录>"
```

Claude Code 把 `Codex` 改为 `Claude`。为 `SessionStart`、`UserPromptSubmit`、`Stop`、`SubagentStart` 四个事件配置同一命令；建议超时 5 秒。Codex Windows Hook 使用其 `commandWindows` 字段，Claude 使用当前版本支持的 `command` 格式。

启动 Hook 注入真实会话 ID、工作区和根身份。新用户输入重置收尾状态；漏保存时提醒一次。`stop_hook_active` 为真时不再阻止结束，避免 Codex 将 Hook 续答变为新提示时循环。子代理仅接收回传要求，不调用共享保存。适配器不读取会话文件、工具结果或密钥。

Codex 的新 Hook 必须通过宿主原生信任机制；只配置命令不代表已启用。不要使用跳过信任的运行参数。

## PI-Desktop：原生 MCP 接入与退出

已在 PI-Desktop 0.15.10 验证原生 MCP 接入，无需安装适配器或修改全局 `AGENTS.md`。

在 PI-Desktop 的“设置 → MCP → 新增”中选择全局级、本地程序（stdio），填写：

- 标识符：`shared_brain`；名称：`Shared Brain 0.4.1`。
- 可执行文件：`<Shared Brain 安装目录>/shared-brain.exe`。
- 参数：`--home "<应用数据目录>" mcp`，与 Shared Brain 桌面使用同一个 home。
- 环境变量留空，不填写模型密钥；知识库路径继续由 Shared Brain 设置管理。

保存并启用，点击“测试连接”，应显示 5 个工具。随后新建任务，询问已有项目经验；PI-Desktop 可通过原生 `ToolSearch` 按需发现 `mcp_shared_brain_search`。本方式已验证 Agent 主动检索，不安装启动、失败或收尾 Hook，也不保证每次会话自动 bootstrap 或保存。需要保存时明确要求 Agent 按工具说明执行。

**当前项目临时脱离：**

1. 等当前回合结束。
2. 打开“设置 → MCP”，在“选择项目”中选中要停用的项目，关闭 `Shared Brain 0.4.1` 右侧开关。
3. 在该项目中新建任务继续使用 PI-Desktop。旧聊天里已经读入的知识仍在聊天历史中。

PI-Desktop 0.15.10 的开关按选中的项目生效；连接旁的“全局”标签表示配置的保存位置，不代表此开关会停用所有项目。恢复时选中同一项目，打开开关并新建任务。仅关闭 Shared Brain 桌面窗口不能代替停用连接。

**所有项目彻底脱离：**等当前回合结束，在“设置 → MCP”中打开该全局服务器的“操作 → 移除”，再点“再点一次删除”，随后新建任务。本接入已实测：移除后全局及项目查询均无该连接，它启动的 Shared Brain 后台进程退出。普通模型对话仍可用；要重新接入，按上面的字段新增连接即可。

永久退出时可再按需退出或卸载 Shared Brain 程序。知识库 Markdown、Obsidian 资料及 PI-Desktop 聊天保留，不需要迁移，也不要删除整个 `.agents` 或 `.pi` 目录。全局连接项由 PI-Desktop 保存在 `~/.agents/servers/shared_brain.json`；本接入没有额外写入全局指令或 Hook，因此无需清理其他入口。

## 旧 Oh-DSH Desktop

安装目录中的 Harness 0.1.2 已带 `@deepseek-ai/dsh-mcp-client`，复用它，无需新增工具桥。在当前 profile 的 `cordis.patch.yml` 中添加两项 `insert`：

- `shared-brain-mcp`：`name: '@deepseek-ai/dsh-mcp-client'`，config 中设置 `serverName: shared_brain`、`transport: stdio`、command 和 args。
- `shared-brain-lifecycle`：name 指向 `oh-dsh.mjs` 的 file URL，config 包含同一 command 和 home。

生命周期使用旧宿主实际的 `agent/session-start`、`agent/pre-step`、`agent/turn-stopping`，身份来自 `agents.roots()` 与 `session.id`。只有 `source.kind=user` 的输入重置收尾状态，插件补写不重置。保留原 profile 的其他项及 `!!js` 表达式，不把整份 YAML 用普通 JSON/YAML 序列化覆盖。

DeepSeek Harness 0.1.7 继续使用 `deepseek-harness.mjs`，其启动事件和会话字段不同。

## 切换与验证

宿主配置修改前保存同目录 `.bak`。替换旧 DSH 的自动启动、保存 Hook 和 MCP 入口，保留其他 Hook。用户级薄入口和工作区入口需一致；旧 DSH 文档留作历史，不能继续覆盖新会话的启动规则。

重开宿主或新会话后确认五工具及新的 `task`/`changes` 参数可见、启动身份正确、全局提示词完整、知识范围正确、项目保存可读回、独立会话不写总结、电脑变更更新公共对象、收尾只提醒一次。新知识库默认不继承旧库项目；未匹配路径不创建项目卡。已有项目在项目配置页补充路径；更新适配文件及薄入口后重启 MCP 连接，移除旧的归属询问和独立会话强制总结规则。

关闭接入时移除 `shared_brain` MCP 和上述适配器 Hook/profile 项；若要恢复旧 DSH，只从本机 `.bak` 恢复对应配置，知识库资料保留。

本地测试覆盖两种原生命令 Hook 和两种 Harness 适配器，使用真实 EXE 与隔离数据目录。Codex 桌面内核可列出五工具，Claude CLI 可建立 MCP 连接，旧 Oh-DSH 已安装的客户端可注册五工具。模型实际遵循与用户真实跨宿主任务接力需要在新会话确认，测试不会代替用户发起付费请求。

包内 Node.js 只执行命令 Hook；Oh-DSH 和 DeepSeek Harness 插件继续由宿主自己的运行时加载。Node.js 及其依赖的许可声明随包保存在 `runtime/NODE-LICENSE.txt`。
