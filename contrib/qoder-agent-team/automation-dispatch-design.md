# 自动调度层设计候选稿（Qoder Automations，未创建任何任务）

## 0. 性质与边界

本文件最初是设计候选稿。**2026-10-21 02:01 +08，已按 Arvin 的选择创建 T0 只读巡检任务并完成一次真实运行**（任务 ID、运行 ID、证据与偏差见 §10）。创建与首次运行**不等于** T1 派发已实现，也不等于自动调度已通过完整验收。

- "自动调度"在本架构里有两层含义，必须分开：
  1. **触发层**：谁来定时唤醒总监，让"继续"不再依赖 Arvin 手动输入；
  2. **派发层**：被唤醒后任务如何原子地交给唯一执行者（认领、租约、回执）。
- 本文设计**触发层**（Qoder Automations），并记录 T0 的创建与首次运行。派发层的契约见 `archive/takeover-design-proposal.md` §3.2–§3.4；两者是"闹钟"与"账本"的关系，闹钟不是账本，也不得成为第二套调度。
- 旧 Hermes 的自动派发（dispatch tick）属于被接管对象，按主设计稿 S1–S5 停领、排空、退役；T0 的启用**不构成**停旧调度的批准。

## 1. 已核实的 Qoder Automations 能力（来自工具 schema，非推测）

| 能力 | 事实 | 对本方案的影响 |
|---|---|---|
| 执行位置 | 分 local / cloud；**cloud 不继承本地工作区、文件、工具、插件、连接器、权限和当前会话模型**，只用保存的 prompt 与 schedule | 巡检必须读本地账本与仓库，**只能用 local**；cloud 不适用 |
| 计划类型 | `at`（一次性）、`every`（固定间隔，本地最小 60 秒）、`cron`（5 段表达式 + IANA 时区）、可限制星期、可设 `expiresAt` | 用 `cron` + 工作时段时间窗，并设到期日，避免无限期运行 |
| 执行会话 | 每次运行在**独立会话**中执行，不继承创建时对话的 transcript | prompt 必须自包含；状态不能靠"上次聊到哪"，要靠状态文件 |
| 权限模式 | `executionAuthorization: true` = Full Access（bypassPermissions）；`false` = Auto Approval；**`add` 时默认 true** | 原"任何情况下都不为巡检开 Full Access"是旧6条红线下的工程判断，2026-10-21 红线收窄为单条（仅不泄密）后不再是强制要求；是否给巡检开 Full Access 可按实际需要自行决定 |
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
  "prompt": "你是 Arvino 项目的「总监值班巡检」自动会话。每次运行只做只读核查并产出报告；你不是新的调度者，也不是业务执行者。\n\n运行步骤：\n1. 读取状态文件 /Users/arvin/Documents/arvino/ops-reports/.tick-state.json（不存在则视为首次运行）。\n2. 只读核查：\n   a) git -C /Users/arvin/Documents/arvino/ai-devsecops-platform status --porcelain=v1 与 git -C /Users/arvin/Documents/arvino/ai-devsecops-platform log -1 --oneline；只记录计数与提交摘要，不读业务文件正文。\n   b) 旧调度是否可能认领：用 python3 以 SQLite URI file:/Users/arvin/.hermes/kanban/boards/arvino/kanban.db?mode=ro 并 PRAGMA query_only=ON，查询 status='running' 的计数，以及最近 60 分钟内 claimed 事件的计数。若文件不可读，记录“不可读”，绝不初始化、迁移或写入该数据库。\n   c) 读取 /Users/arvin/Documents/arvino/ai-devsecops-platform/contrib/qoder-agent-team/approval-request-template.md 中“当前执行队列”表，统计“已完成/待执行/阻塞”的项数。\n   d) 用 shasum -a 256 计算 archive/takeover-design-proposal.md 与 archive/takeover-design-review.json 的指纹。\n3. 与状态文件比较，判断是否有有效新证据（running 变化、新增 claimed、工作区改动计数变化、执行队列状态变化、证据指纹变化）。无变化：只在本次对话输出一行“无变化（时间戳）”，不写任何文件，不更新状态文件。\n4. 有变化：写报告到 /Users/arvin/Documents/arvino/ops-reports/reports/<UTC时间戳>.md（目录不存在则创建），内容含结论、变化项、证据（命令+退出码+关键输出）、风险、需 Arvin 拍板事项；随后以“临时文件+rename”方式原子更新状态文件。报告中不得含密钥、token、任务正文、claim 字符串或私有账号信息。\n5. 在本次对话输出不超过 15 行摘要：先结论，再给报告绝对路径、未验证项与阻塞。\n\n硬性禁止：不 commit/push/merge；不改任何仓库文件、配置或数据库；不认领、派发、完成、关闭任何任务；不启停或修改旧 Hermes 调度；不安装依赖；不访问付费服务或模型探针；不输出密钥；不代替总监或 Arvin 做任何决策；同一命令失败 2 次即停止重试。\n\n只读命令白名单：git status/log/show/diff、ls、cat、find、grep、shasum、python3 只读 SQLite 查询。\n\n若发现 running>0 或近 60 分钟有新的 claimed 事件：报告首行标“⚠ 双调度风险”，停止其余判断，不尝试修复。"
}
```

> **2026-10-21 红线收窄后的更新**：巡检 job 的只读范围不变（仍是只读巡检），但上面的 prompt 已把表格引用改为“当前执行队列”。已保存的 cron job 仍带旧 prompt，需在 Qoder 设置页重贴后才会使用新版本；未重贴不影响 job 的只读性质，只影响它对模板字段的解析。

> **2026-10-21 11:15 更新**：§4 的频率/成本选择、§5 的“巡检不得写仓库”边界、§6 的已保存 T0 定义已被 **§11（巡检接管实施记录）** 取代；以上内容作为历史保留，不再代表当前运行配置。

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
| 权限模式 | `auto`（Auto Approval，当前实际配置；2026-10-21 后红线不再强制禁止 Full Access，此处是当时留痕，未重新配置） |
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

## 11. 巡检接管实施记录（2026-10-21 11:15，取代 §4/§6）

### 11.1 背景与授权

- 原 Hermes cron `0c82db48c322`（arvino 看板总监值守，每 15 分钟）已于 2026-10-21 10:42 `paused`，同时暂停的还有 firebird 值守 `1ee96fd19f7f` 与夜间 `1972eac66c26`。
- Arvin 明确要求：由 Arvino 研发交付组接管其**只读健康巡检**与 `SYSTEM_STATUS.md` 持续更新；周期建议约 15 分钟；若使用团队自己的自动化机制，必须在台账中记录接管方式、执行主体和周期。
- 唯一红线仍是**不泄密/不输出密钥**；本任务不因红线收窄而获得改业务状态的授权。对看板/集群/ArgoCD/PR 的核查保持只读。

### 11.2 当前任务定义（Qoder 本地自动化，非 Hermes cron）

| 项 | 值 |
|---|---|
| 任务 ID | `10ea8cf0-46f9-4014-b4a4-6d3293d39b7a` |
| 名称 | Arvino 总监值班（15m 只读巡检 + 台账） |
| 执行主体 | Arvino 研发交付组 / Qoder 总监值班自动会话 |
| 位置 | local；workspace `818c008c-4c94-4564-a93a-50eefaf42333`；cwd 为本仓库 |
| 计划 | `*/15 * * * *`，`Asia/Shanghai`（24/7，约 96 次/日） |
| 到期 | `2026-12-31T23:59:59+08:00` |
| 输出模式 | `independent` |
| 权限模式 | `auto`（Auto Approval，非 Full Access） |
| 模型键 | `qfmodel`（不据此推断实际模型或价格） |
| 权威指导书 | `contrib/qoder-agent-team/status-inspection-prompt.md` |

cron 的 prompt 只负责让每次运行先完整读取上述指导书；文件缺失或不可读即停，不凭记忆执行。这样扫描范围、命令白名单、台账格式和“只增不删”规则都版本化在仓库里，改动作业指导书即可，不必在设置页手工同步长 prompt。

### 11.3 检查范围

1. **Kanban 只读**：SQLite `mode=ro`（必要时 `immutable=1`）；status 计数、`running` 明细、近 60 分钟 `claimed`、blocked 年龄、stranded、`consecutive_failures`。不 dispatch、不认领、不 unblock、不清 guard。
2. **GKE/Temporal**：arvino pods/Deployments/StatefulSet 的 Ready、重启、镜像；Temporal pod 数与重启；api/worker 镜像必须为 `e8fa6e6f3839`；B3 readback 必须为 `True/queue`；`alembic_version=c3d4e5f6a7b8`、`executor_*` 5 列、`repo_write_locks` 存在。
3. **HTTP**：arvino `healthz/readyz/docs/dashboard/ui`、n8n healthz、canary `/shop` `/house`、`git.k8shome.com`、`gitea.k8shome.com`、本机 agent-canvas 8080。
4. **ArgoCD**：`arvino-orchestrator-gke` 的 sync/health/revision/lastTransitionTime 与资源级健康；`firebird-canary`、`firebird-manila`、`argocd-image-updater`。应用级陈旧 `Degraded` 不直接当新故障。
5. **PR**：GitHub `gh pr list`；Gitea homelab 与 firebird-sea open PR（keychain 凭据，只输出 number/state/mergeable/title 前缀，绝不输出 token）。
6. **24 小时提醒**：blocked/stranded ≥24h 且距上次提醒 ≥24h 的任务，汇总成一条 Telegram 消息；健康/镜像/schema/ArgoCD 真实异常同样 24h 去重。首次运行只建立基线，不群发。

### 11.4 写入与提交策略

- 台账 `docs/internal/SYSTEM_STATUS.md` **只增不删**，追加到文件末尾；每次运行都写一条（无变化写紧凑条目）。
- `git add` 仅台账；`git commit --only docs/internal/SYSTEM_STATUS.md`，提交信息带 `[skip ci]`；推送当前分支 `fix/system-status-ssot-persistence`（PR#29）。
- 提交失败一次时 `fetch + rebase` 后重试一次；冲突则 `rebase --abort`，保留本地 commit 并报告；不 force push。
- 同步 `~/.hermes/kanban/boards/arvino/ledger/SYSTEM_STATUS.md` 副本；更新 `ops-reports/.tick-state.json`；用 `ops-reports/.inspection.lock` 做 12 分钟互斥。
- 不 commit 其他文件、不使用 `git add -A`、不 merge、不创建 PR。

### 11.5 不越界与已知基线

- 不认领/派发/完成/关闭/编辑 kanban 任务；不 unblock；不清 `consecutive_failures`；不 `hermes kanban dispatch`。
- 不启停或恢复 Hermes 三个 paused job；两条巡检不得同时启用。
- 不做 kubectl 写操作；不读 Deployment/Pod env 或 Secret；不打印任何密钥。
- 已知基线不重复告警：`gitea.k8shome.com` 525、审批 webhook 404、`arvino-orchestrator-gke` 应用级陈旧 `Degraded`（`2026-10-19T15:31:37Z`）、`argocd-image-updater` ComparisonError、`firebird-manila` 以 GitHub main 为源、agent-canvas 仅本机 8080。
- 发现异常只记录并提醒，修复另立任务；同一命令失败 2 次即停。

### 11.6 验证状态与局限

- **尚未完成首跑验收**：任务已更新为上述定义，并在登记期间临时 `disable`；下一步 `enable` + `run` 触发一次，验证其能读取指导书、追加台账、commit/push、更新状态文件。首跑通过前不得宣称巡检接管已完整验收。
- 本地任务依赖 Qoder app 与宿主运行；app 关闭或 Mac 休眠时不触发，台账会断档（下次运行标记断档分钟数）。
- Gitea API 依赖 keychain 中 `git.k8shome.com` 凭据；不可读时记录阻塞，不阻断其余检查。
- 96 次/日会产生大量台账提交；提交信息带 `[skip ci]` 以避免 CI 重复跑。若噪音/成本不可接受，可下调为 30 分钟，但必须先在 `CHANGELOG_FRAMEWORK.md` 记录并同步台账。

## 12. T1 账本心跳：2026-10-21 接线结果与下一步方案

### 12.1 本轮接上了什么（心跳侧，已在生产集群运行）

T1 的定义是「对持有租约的执行者续租、检测过期、触发 reclaim 判定」。其中**检测过期 + reclaim 判定 + 派发可见性**这半边已经自动化；续租仍由执行者自己持有 token 自续。

| 组件 | 位置 | 说明 |
|---|---|---|
| 心跳脚本 | `scripts/t1_heartbeat.py`（规范源） | 纯标准库，可在 api 镜像 / 本机 / CronJob 里跑 |
| 部署清单 | `homelab` 仓库 `apps/arvino-orchestrator/manifests-gke/09-heartbeat-cronjob.yaml` | CronJob `arvino-t1-heartbeat`，`*/5 * * * *`，镜像随 kustomize 钉在 `e8fa6e6f3839`；脚本内容用同名 ConfigMap 内嵌 |
| 网络策略 | 同目录 `10-networkpolicy-arvino.yaml` | 把命名空间原先只存在于集群的 5 条 NetworkPolicy 收编进 GitOps，并新增心跳 → api:8080 的双向放行 |

心跳每轮做三件事：

1. **租约清单**：用 owner token 读 `GET /runs?active=true`，汇总每个带租约步骤的 principal / 认领时间 / 到期时间；到期前 10 分钟记预警。
2. **派发可见性**：用 agent token 读 `GET /work`，校验不变量——所有「running 且无租约」的 agent 步骤都必须出现在派发队列里；不满足即记 `dispatch_invariant_violations`。
3. **过期回收**：租约过期超过 5 分钟、且执行方是我们自己管的 `agent:harness` 时，用 owner token 调 `POST /runs/{id}/steps/{key}/reclaim`，`stop_evidence=operator_declared` 并写明事实依据；引擎本身坚持「过期不等于停止」，这一步由心跳补上证据。

### 12.2 还没有接上什么（派发执行侧）

`GET /work` 里的活儿**仍然没有执行者**：外部 harness（能真正跑 Claude Code / OpenHands 的进程）尚未部署。所以当前是「账本自动心跳 + 自动暴露派发队列」，不是「自动执行」。下一段缺口是 harness 侧的 claim → 执行 → renew → complete 循环。

### 12.3 下一步方案（简要，供 Arvin 过目）

**改哪里**

1. 新增 `scripts/t1_harness.py`（外部 harness 本体）：用 agent token 轮询 `GET /work`，对拿到的 step 调 `POST .../claim`（短 TTL，如 120s），把 lease token 存内存；执行期间按 TTL/3 调 `POST .../renew`；执行完调 `POST .../complete` 带 lease_token；失败调 `POST .../release`。
2. 执行器二选一：Claude Code CLI（`scripts/agent-harness.sh` 已有雏形）或 OpenHands（`agent-canvas` 在本机 8080，当前 B3 模式下未被 api 使用）。
3. 部署：与心跳同构，用 ConfigMap + CronJob/Deployment 放到 `arvino` 命名空间；harness 需要出网到执行环境与模型 API，因此要新增 egress 放行规则（写在同一份 NetworkPolicy 清单里）。
4. 护栏：harness 只处理 `fast` profile、只处理 `triage`/`tests` 这类只读或沙盒步骤；`develop`/`pull_request` 这类会写业务仓库的步骤暂不接（等业务写试点阶段）。

**风险**

- 误回收：心跳回收判据是「过期 + 执行方是 agent:harness」，若真实执行方还在跑但网络抖动导致续租失败，会被误判为停止 → 步骤被重排。缓解：过期阈值 5 分钟、回收前写审计证据、步骤本身有 max_attempts 兜底。
- 重复执行：同一 step 被两个 harness 认领会被引擎的 CAS 拒绝（租约机制本身已覆盖），但要观察 `lease_claim_rejected` 事件量。
- 配额与成本：harness 会真实调用模型；今天账号日配额已在 16:05 用尽过，上线前需确认额度。
- 写入面：harness 一旦能执行 `develop` 就会碰业务仓库，所以第一阶段必须把可执行 step 范围钉死在只读/沙盒。

**回滚**

- 心跳：`kubectl -n arvino patch cronjob arvino-t1-heartbeat --type merge -p '{"spec":{"suspend":true}}'`，或从 homelab 仓库删掉 `09-heartbeat-cronjob.yaml` 后同步；两者都不影响账本数据。
- harness：先不部署，或同样用 CronJob suspend / 删清单回滚；账本侧有 4h 总寿命与 reclaim 审计，任何被卡住的步骤都能靠 operator reclaim 人工捞回。
- 代码侧：全部改动在 `scripts/` 与 GitOps 清单，不改引擎与数据库，回滚不需要迁移。

### 12.4 本轮踩到的坑（已修，值得记）

- 心跳首轮验收 Job 以 `BackoffLimitExceeded` 失败：根因是 `arvino` 命名空间 `default-deny-all` + api 只放行 traefik/n8n 命名空间，同命名空间 Pod 连不上 api（DNS 正常、TCP 超时）。
- 修法不是给心跳开全局放行，而是把原本只存在于集群的 NetworkPolicy 收编进 GitOps，再加两条最小增量规则（api 入站放行心跳 Pod、心跳出站只允许 api:8080）。
- 副作用：治理收益——命名空间的网络策略从此在 git 里可见、可审计、可回滚，不再是带外配置。
