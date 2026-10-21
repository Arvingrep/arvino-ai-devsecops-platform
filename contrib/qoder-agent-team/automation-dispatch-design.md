# 自动调度层设计候选稿（Qoder Automations，未创建任何任务）

## 0. 性质与边界

本文件最初是设计候选稿。**2026-10-21 02:01 +08，已按 Arvin 的选择创建 T0 只读巡检任务并完成一次真实运行**（任务 ID、运行 ID、证据与偏差见 §10）。创建与首次运行**不等于** T1 派发已实现，也不等于自动调度已通过完整验收。

- "自动调度"在本架构里有两层含义，必须分开：
  1. **触发层**：谁来定时唤醒总监，让"继续"不再依赖 Arvin 手动输入；
  2. **派发层**：被唤醒后任务如何原子地交给唯一执行者（认领、租约、回执）。
- 本文设计**触发层**（Qoder Automations），并记录 T0 的创建与首次运行。派发层的契约见 `takeover-design-proposal.md` §3.2–§3.4；两者是"闹钟"与"账本"的关系，闹钟不是账本，也不得成为第二套调度。
- 旧 Hermes 的自动派发（dispatch tick）属于被接管对象，按主设计稿 S1–S5 停领、排空、退役；T0 的启用**不构成**停旧调度的批准。

## 1. 已核实的 Qoder Automations 能力（来自工具 schema，非推测）

| 能力 | 事实 | 对本方案的影响 |
|---|---|---|
| 执行位置 | 分 local / cloud；**cloud 不继承本地工作区、文件、工具、插件、连接器、权限和当前会话模型**，只用保存的 prompt 与 schedule | 巡检必须读本地账本与仓库，**只能用 local**；cloud 不适用 |
| 计划类型 | `at`（一次性）、`every`（固定间隔，本地最小 60 秒）、`cron`（5 段表达式 + IANA 时区）、可限制星期、可设 `expiresAt` | 用 `cron` + 工作时段时间窗，并设到期日，避免无限期运行 |
| 执行会话 | 每次运行在**独立会话**中执行，不继承创建时对话的 transcript | prompt 必须自包含；状态不能靠"上次聊到哪"，要靠状态文件 |
| 权限模式 | `executionAuthorization: true` = Full Access（bypassPermissions）；`false` = Auto Approval；**`add` 时默认 true** | 必须显式传 `false`；任何情况下都不为巡检开 Full Access |
| 输出模式 | `independent`（每次新建会话）/ `merged`（复用任务当前执行会话，不可用时新建） | 巡检用 `independent`，避免会话上下文累积导致成本上升与行为漂移 |
| 模型 | local add 省略则继承**创建时会话的模型**并固定保存，之后不跟随变化 | 不在本层引入额外模型；不据模型名推断价格 |
| 管理 | `list / add / update / remove / run / enable / disable`；本地任务 ID 可管理；cloud ID 不可用本工具管理 | 具备 disable 与 remove 的停机手段（kill switch） |
| 完成语义 | 保存任务或接受一次运行**不等于**工作已完成 | 报告以运行产出的文件与账本为准，不以"任务已触发"当成果 |

## 2. 触发层分级（T0 已上线；T1/T2 待实施）

| 级别 | 名称 | 做什么 | 前置 | 状态 |
|---|---|---|---|---|
| T0 | 只读巡检 tick | 定时读仓库状态、旧调度是否认领、待审批清单、证据指纹；有变化写报告，无变化不写 | 用户确认任务配置 | **本稿建议先做这一级** |
| T1 | 账本心跳 | 对持有租约的执行者续租、检测过期、触发 reclaim 判定 | 主设计稿 §3.2 已实现于候选分支 | 租约机制已实现；调度层尚未接线 |
| T2 | 事件触发 | 新 Issue / webhook 到达时一次性唤醒（`at` 或外部入口） | 外部入口、鉴权与写入路径 | 未设计 |

T0 的边界：**不认领、不派发、不完成任何任务**，只产出可审计的巡检报告。它是"让 Arvin 不必每天手动问进度"的可见性层，不是执行层。

## 3. T0 tick 契约（幂等、可审计、可停机）

1. **无状态执行**：每次运行从状态文件重建"上次看到什么"，不依赖会话记忆。
2. **只读命令白名单**：`git status/log/show/diff`、`ls/cat/find/grep`、`shasum`、`python3` 只读 SQLite（`mode=ro` + `PRAGMA query_only=ON`）。
3. **有效新证据才产出**：与状态文件比较 `running` 数量、近 60 分钟新增 `claimed` 事件、工作区改动计数、审批清单状态、证据指纹；无变化只输出一行"无变化"，不写文件。
4. **报告落盘在仓库之外**：`/Users/arvin/Documents/arvino/ops-reports/reports/<UTC时间戳>.md`，状态文件 `…/ops-reports/.tick-state.json`。**不写仓库内文件**，不产生需要 commit 的脏工作区。
5. **双调度检测**：若旧看板 `running > 0` 或近 60 分钟出现新的 `claimed` 事件，报告首行标"⚠ 双调度风险"并停止其余判断，**不尝试修复**。
6. **失败熔断**：同一命令失败 2 次即停止重试；连续两次运行失败则不更新状态文件并醒目报告。
7. **脱敏**：报告不包含密钥、token、任务正文、claim 字符串、私有账号信息。

## 4. 频率与成本

- 每次运行都会消耗 Credit；消耗取决于 token、模型与工具调用，**不能按模型名推断价格**（工具文档明确要求不得推断定价）。
- 建议从最低有效频率起步，观察一到两周再决定是否加密：

| 方案 | 计划 | 每日运行次数 | 适用 |
|---|---|---|---|
| A（推荐） | `*/30 9-18 * * 1-5`，Asia/Shanghai | 约 20 次（仅工作日白天） | 需要及时看到阻塞/审批变化 |
| B | `0 * * * *` | 24 次 | 全天值守，成本更高 |
| C | `0 9 * * *` | 1 次 | 只要每日摘要，最省 |
| D | `*/120 9-19 * * 1-5` | 约 6 次 | 折中 |

- 统一设 `expiresAt`（建议首次 14 天后），到期自动停止，强制复核后续期，避免"设完就忘"。
- `executionAuthorization` 固定 `false`（Auto Approval），**不传 true**。
- `outputMode` 固定 `independent`。

## 5. 安全栏（prompt 内硬约束）

> 说明：本节的“不 commit/push/merge、不改仓库、不生产写”等是**巡检 job 自身的只读范围**，不是公司红线。公司红线只有**不泄密/不输出密钥**；不要把这组只读限制套到其他执行角色或据此阻塞正常任务。

巡检 prompt 必须逐条写明：

- 不 commit / push / merge；不改仓库、配置、数据库；
- 不认领、派发、完成、关闭任何任务；
- 不启停或修改旧 Hermes 调度；
- 不安装依赖、不访问付费服务、不跑模型探针；
- 不输出密钥；不代替 Arvin 批准任何动作；
- 只读命令白名单之外的动作一律不做；
- 发现双调度风险只报告不修复。

## 6. 已保存的任务定义（2026-10-21 创建，首次运行记录见 §10）

```json
{
  "name": "Arvino 总监值班（只读巡检）",
  "location": "local",
  "schedule": {
    "kind": "cron",
    "expression": "*/30 9-18 * * 1-5",
    "timeZone": "Asia/Shanghai"
  },
  "outputMode": "independent",
  "executionAuthorization": false,
  "expiresAt": "2026-11-04T23:59:59+08:00",
  "cwd": "/Users/arvin/Documents/arvino/ai-devsecops-platform",
  "prompt": "你是 Arvino 项目的「总监值班巡检」自动会话。每次运行只做只读核查并产出报告；你不是新的调度者，也不是业务执行者。\n\n运行步骤：\n1. 读取状态文件 /Users/arvin/Documents/arvino/ops-reports/.tick-state.json（不存在则视为首次运行）。\n2. 只读核查：\n   a) git -C /Users/arvin/Documents/arvino/ai-devsecops-platform status --porcelain=v1 与 git -C /Users/arvin/Documents/arvino/ai-devsecops-platform log -1 --oneline；只记录计数与提交摘要，不读业务文件正文。\n   b) 旧调度是否可能认领：用 python3 以 SQLite URI file:/Users/arvin/.hermes/kanban/boards/arvino/kanban.db?mode=ro 并 PRAGMA query_only=ON，查询 status='running' 的计数，以及最近 60 分钟内 claimed 事件的计数。若文件不可读，记录“不可读”，绝不初始化、迁移或写入该数据库。\n   c) 读取 /Users/arvin/Documents/arvino/ai-devsecops-platform/contrib/qoder-agent-team/approval-request-template.md 中“当前执行队列”表，统计“已完成/待执行/阻塞”的项数。\n   d) 用 shasum -a 256 计算 takeover-design-proposal.md 与 takeover-design-review.json 的指纹。\n3. 与状态文件比较，判断是否有有效新证据（running 变化、新增 claimed、工作区改动计数变化、执行队列状态变化、证据指纹变化）。无变化：只在本次对话输出一行“无变化（时间戳）”，不写任何文件，不更新状态文件。\n4. 有变化：写报告到 /Users/arvin/Documents/arvino/ops-reports/reports/<UTC时间戳>.md（目录不存在则创建），内容含结论、变化项、证据（命令+退出码+关键输出）、风险、需 Arvin 拍板事项；随后以“临时文件+rename”方式原子更新状态文件。报告中不得含密钥、token、任务正文、claim 字符串或私有账号信息。\n5. 在本次对话输出不超过 15 行摘要：先结论，再给报告绝对路径、未验证项与阻塞。\n\n硬性禁止：不 commit/push/merge；不改任何仓库文件、配置或数据库；不认领、派发、完成、关闭任何任务；不启停或修改旧 Hermes 调度；不安装依赖；不访问付费服务或模型探针；不输出密钥；不代替总监或 Arvin 做任何决策；同一命令失败 2 次即停止重试。\n\n只读命令白名单：git status/log/show/diff、ls、cat、find、grep、shasum、python3 只读 SQLite 查询。\n\n若发现 running>0 或近 60 分钟有新的 claimed 事件：报告首行标“⚠ 双调度风险”，停止其余判断，不尝试修复。"
}
```

> **2026-10-21 红线收窄后的更新**：巡检 job 的只读范围不变（仍是只读巡检），但上面的 prompt 已把表格引用改为“当前执行队列”。已保存的 cron job 仍带旧 prompt，需在 Qoder 设置页重贴后才会使用新版本；未重贴不影响 job 的只读性质，只影响它对模板字段的解析。

## 7. 启用前置与首次运行验证

1. **用户确认**：位置（local）、计划、prompt、输出模式与到期日逐项确认后才 `add`。
2. **首次运行验证**：
   - 确认写权限：报告与状态文件落在仓库外，首次运行若被权限拦住，记录为阻塞，不放宽为写仓库内文件；
   - 确认只读边界：检查本次运行的命令均来自白名单；
   - 确认状态文件与报告格式可读、可对比。
3. **到期复核**：`expiresAt` 到期后任务自动停止；续期前先看已有报告是否有价值，无价值则 `remove`。
4. **停机手段**：`disable` 立即停用，`remove` 删除；两者都不影响账本与业务。

## 8. 与主设计稿的关系

- T0 只是可见性层：它让"轮到你决策"这件事有固定节奏与证据，但**不会**让任务自动开始执行。
- T1 才是自动派发的核心：续租与过期检测必须复用账本租约契约，不能用"会话还开着"推断执行者存活。
- 旧 Hermes dispatch tick 与本层**不是并存关系**：S1–S5 完成前，本层只能只读；完成切换后，旧 tick 退役，Qoder 侧才按 T1 承担派发。
- 本层不新建账本、不复制状态机、不把自动化会话日志当业务状态。

## 9. 明确不主张

- 不主张 T0 已验证完整覆盖所有异常路径（只完成一次真实首跑，见 §10）。
- 不主张 T0 能派发任务、能续租、能判定执行者存活。
- 不主张自动运行免费；价格不可按模型名推断。
- 不主张本层的启用等于旧调度可以停止。

## 10. 实施记录：T0 创建与首次运行（2026-10-21）

### 10.1 任务与运行

| 项 | 值 |
|---|---|
| 任务 ID | `10ea8cf0-46f9-4014-b4a4-6d3293d39b7a` |
| 运行 ID | `1b01a6f5-baa9-40ff-9c76-d23e975b2ad3` |
| 计划 | `*/30 9-18 * * 1-5`，Asia/Shanghai（约 20 次/工作日） |
| 下次运行 | 2026-10-21T09:00:00+08:00 |
| 到期 | 2026-11-04T23:59:59+08:00 |
| 权限模式 | `auto`（Auto Approval，**非** Full Access） |
| 输出模式 | `independent` |
| 执行范围 | workspace `818c008c-4c94-4564-a93a-50eefaf42333`，cwd 为本仓库 |
| 首次运行结果 | `succeeded`，耗时 138,426 ms |
| 模型键 | 创建响应报 `dfmodel`，运行后列表报 `qfmodel`；差异原因未核，**不据此推断实际模型或价格** |

### 10.2 首次运行产出（已由总监复核）

- 报告：`/Users/arvin/Documents/arvino/ops-reports/reports/2026-10-20T180120Z.md`（3.3 KB）
- 状态：`/Users/arvin/Documents/arvino/ops-reports/.tick-state.json`（JSON 可解析，`last_run_utc=2026-10-20T18:01:20Z`）
- 报告结论：旧看板 `running=0`、近 60 分钟 `claimed=0`，无"双调度风险"；工作区 4 项未提交改动；审批清单 3 项已具备 / 5 项未具备。
- 交叉核对：报告引用的两个证据 sha256 与磁盘现值一致（提案 `e40003ae…`、审查回执 `9d7fffe5…`）；审批计数与模板表一致；仓库 `git status` 在运行前后同为 4 项，**tick 未写仓库**。

### 10.3 偏差与限制

1. 首次运行用 `mode=ro&immutable=1` 读旧看板（报告自述 `mode=ro` 首次打开失败），属只读快照，可能滞后于 WAL 实时数据；未执行初始化、迁移或写入。
2. 首次运行前无状态文件，报告把"基线不存在"列为风险——这是首跑的正常产物，第二次运行起应做增量比较（待观察）。
3. 报告里"需 Arvin 决策事项"第 2 条（`.qoder/` 与 `contrib/` 的提交时机）属于在途工作提醒，不构成提交授权。
4. 本记录只覆盖**一次**真实运行；异常路径（报告目录不可写、旧看板不可读、连续失败熔断）尚未被真实触发验证。
5. T0 仍不具备任何派发能力；T1/T2 未实现、未批准。
