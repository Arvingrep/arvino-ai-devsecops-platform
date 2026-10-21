# Arvino 调度接管设计候选稿（历史设计稿；核心已实施，保留留痕）

> **2026-10-21 归档说明**：本文写于红线收窄前。文中“待批准/未获批准/不做生产写/不 commit”等表述均为**当时范围**。现红线只剩**不泄密/不输出密钥**，原五条与 L0–L3 分级作废；方案与执行由总监按任务目标决策并留证。本文仅保留设计留痕，不再作为审批依据。

## 0. 文档性质与边界

本文件是**设计候选稿**，不是方案批准、不是实现许可、不是上线声明。

- 依据：`stage-1-verification.json`（阶段回执）、`takeover-preflight.json`（只读接管预检）、`issue-67-anonymous-baseline.json`（匿名基线）、`TEAM.md` 与十个正式角色 md。
- 现状：Qoder 侧十职责首批五席已注册；接管方向已由 Arvin 确认，候选分支已实施核心租约机制（历史表述中的“未获批准”指本文写作时点）。
- 本稿只写"应怎样接线、怎样验收、怎样回退"，不写已接线的运行事实；后续实施证据见 `t1-step-leases-evidence.md`。
- 本稿编写时的范围：不新建仓库 Issue、不改 #67 状态、不关闭或改 Hermes 调度、不 commit/push/merge、不生产写、不付费、不动业务仓库脏工作区；新规则下这些动作由总监按任务目标决策并留证。

## 1. 目标与不变量

接管目标：Qoder 成为唯一调度者（理解需求、拆派、追踪、汇报、申请审批），十个专业职责按需调用；旧 Hermes 调度在被显式停领并排空之前，不与其并行认领写任务。

必须始终成立的不变量：

| 编号 | 不变量 | 现状证据 |
|---|---|---|
| I1 | 同一业务任务同一时刻最多一个真实执行者持有租约 | Hermes 有单任务原子 claim；Arvino 侧仅有步骤派发 CAS |
| I2 | 同一仓库同一时刻最多一个实现者写入 | 两侧都**未强制**（Arvino 无 repo 互斥；Hermes 只锁单任务） |
| I3 | 执行身份来自服务端签发的凭据，不靠 prompt 自报 owner | 现状不成立：Arvino 无 claim token；Hermes claimer 是 host:pid 或调用方自填 |
| I4 | 旧 attempt / 旧 run 的回执不得覆盖新结果 | Arvino 已有 attempt fencing；Hermes 的 `expected_run_id` 可选且存在 `force=True` 覆盖例外 |
| I5 | 权威账本唯一，聊天记录与配置不是账本 | 存在两套账本（Hermes tasks/task_runs 与 Arvino run/step）且无映射 |
| I6 | 独立验收与研发分离，且证据绑定同一候选版本 | 角色配置已定义；运行时隔离未验证 |
| I7 | 任何高影响动作按动作审批并绑定版本 | 角色配置已定义；无运行时强制 |

## 2. 已核验的事实基线（来自 preflight，非推测）

- Arvino 引擎在 `src/arvino/engine.py:503–525` 用 `status=PENDING AND attempt=prev` 的 CAS 派发步骤并自增 attempt，`rowcount != 1` 时视为已被他人认领；`engine.py:916–936` 的 `complete_step` 校验 `status=RUNNING AND attempt=attempt`，不匹配记 `step.stale_completion_ignored` 并返回 `STALE`（同 attempt 已成功则 `DUPLICATE`）。
- `src/arvino/engine.py:1543–1568` 的 `list_work` 只列出 `kind=AGENT AND status=RUNNING AND role 非空` 的步骤，返回 `run_id/step_key/role/attempt/actor/branch_prefix/idempotency_key`（`role` 为空的 AGENT 步不入列表）；**不含任何租约或所有权凭据**。
- `src/arvino/api.py:88–95` 的 `CompleteIn` 只有 `attempt/status/output/artifacts/error/retryable/actor`，**没有 claim token**；`api.py:373–390` 的 `/complete` 与 `/work` 用同一个 `worker` 依赖（`api.py:283`：允许 AGENT/SYSTEM/HUMAN），无法区分"谁持有该步骤"。
- `src/arvino/db.py:88–141` 的 `RunRow`/`StepRow` 无 executor owner、租约、租约到期字段。
- `src/arvino/api.py:214–231` 的鉴权支持静态 token 与 OIDC/JWT，**可以承载外部主体身份**，但当前没有把主体绑定到具体步骤。JWT 的 role claim 映射在 `api.py:198–206`（owner→HUMAN / agent→AGENT / system→SYSTEM），未知 role 直接 401。
- `scripts/agent-harness.sh:292–359` 轮询 `/work` 后直接执行，无外部 claim 证据，也无"同一仓库只允许一个写入者"的检查。
- 仓内**已有**角色路由与执行器实现，不是空白：`src/arvino/adapters/role_router.py` 的 `RoleRouter`（fail-closed，未知角色直接失败）与 `_ROLE_MAP`（现含 backend/architect/qa/security/reviewer/devops/frontend/researcher，**没有** lead/tester/design/smartcontract），已在 `src/arvino/bootstrap.py:169–171` 接线。**不能另建第二套 role 映射。**
- 引擎还有一条**不经 `/work` 与 `/complete` 的内建执行路径**：`engine._dispatch`（`src/arvino/engine.py:562–600`）→ `self.ports.executor.execute(req)` → RoleRouter → `OpenHandsExecutor` / `ClaudeCodeExecutor` / `QueueExecutor`（`bootstrap.py:88–102` 决定优先级；仅 QueueExecutor 才把活交给外部 harness 轮询）。内建路径的结果由引擎内部 `_finalize` 记账（`engine.py:530–534`），**本稿的租约提案默认只覆盖外部拉取路径**，内建路径的兼容策略见 §3.5。
- 仓库身份已有权威来源：`ProjectRow.repo_full_name`（`src/arvino/db.py:65`），`engine.py:580` 已用它作为 repo 传入执行请求；§3.4 的 `repo_key` 应由此派生，不另造来源。
- Hermes 侧：`kanban_db.py:2151–2230` 在事务中以 `ready` + `claim_lock IS NULL` 做单任务 CAS 并建 run；`kanban_db.py:2318–2341` 的 `heartbeat_claim` 可延长 task 与 run TTL，但 CLI `heartbeat`（`kanban.py:312–317` → `kanban_db_dispatch.py:611–646`）只写活动记录、不延长 TTL；`kanban_db.py:2660–2764` 的 `expected_run_id` 可选、且 `force=True`（`kanban_db.py:2712`）是操作者覆盖例外；`kanban_db.py:1021–1028` 默认 claimer 为 `host:pid`，可由调用方自填，**不是认证过的外部主体**。
- 看板快照（`mode=ro` + `query_only=1`，2026-10-20T17:27:18Z）：done 159 / blocked 11 / todo 5 / archived 1 / **running 0**。0 running 只说明当下空闲，不构成"无双调度"的证明。

## 3. 方案 A（推荐）：复用 Arvino 账本，外部执行者租约化

**为什么推荐复用 Arvino 而不是 Hermes 过渡账本**：Arvino 已有事务边界、attempt fencing、审批绑定版本、CI/OPA/GitOps 门禁与审计事件；Hermes 侧即便复用单任务 CAS 与库续租，仍要另造主体校验、执行监督、强制 run fencing 与仓库锁，并多出一套需要长期映射的账本。A 方案把"谁是当前执行者"这件事收敛到已有的持久化账本里，只新增一个正交维度（外部租约），不引入第二份事实源。

### 3.1 数据模型提案（拟改，**未实施**）

在 `StepRow` 上新增正交字段（迁移草案，字段名待评审）：

| 字段 | 含义 | 约束 |
|---|---|---|
| `executor_principal` | 持有租约的服务端认证主体 id（来自 OIDC `sub` 或静态 token 对应主体） | 非空时必须与请求 Principal 一致 |
| `executor_lease_expires_at` | 租约到期时间 | 由服务端在认领/续租时写入，客户端不可自设 |
| `executor_lease_token_hash` | 认领时服务端随机生成、只存哈希的租约令牌 | 回执必须携带明文令牌，服务端比对哈希 |
| `executor_claimed_at` | 认领时刻 | 审计用 |
| `executor_attempt` | 认领时的 attempt 快照 | 与 `attempt` 不一致即视为陈旧租约 |

`RunRow` 侧新增 `repo_key`（规范化仓库标识，如 `owner/repo`）与"仓库写入互斥"所需的占用信息（拟以独立的 `repo_write_locks` 表实现，避免把锁语义塞进 run 行）。`repo_key` **由 `projects.repo_full_name`（`db.py:65`）规范化派生**，`engine.py:580` 已用同一字段作为 repo 身份传入执行请求，不另造来源、不引入第二份仓库命名口径。**不删除、不搬移任何历史数据**；迁移必须幂等且可回滚。

### 3.2 认领 / 续租 / 释放 / 回收契约

拟新增四个接口（路径与命名待评审），前三个要求 `worker` 身份，第四个要求更高权限（`operator`），且**只有具备对应角色且步骤在 `PENDING`/`RUNNING` 窗口内时才可认领**：

| 接口 | 语义 | 关键拒绝条件 |
|---|---|---|
| `POST /runs/{run_id}/steps/{step_key}/claim` | 原子认领：条件更新 `status/attempt`，写 `executor_*` 与租约令牌，返回明文令牌（仅此一次） | 步骤已被他人认领；租约仍有效且主体不同；**旧租约已过期但尚未确认旧执行者停止（须先走 reclaim）**；角色不匹配；`not_before` 未到 |
| `POST /runs/{run_id}/steps/{step_key}/renew` | 续租：仅当前租约持有者可延长，TTL 由服务端上限约束 | 租约已过期；attempt 已变；主体不符 |
| `POST /runs/{run_id}/steps/{step_key}/release` | 主动释放：写审计事件、状态回到可重派 | 主体不符 |
| `POST /runs/{run_id}/steps/{step_key}/reclaim` | **强制回收**：租约过期且已确认旧执行者停止后释放占用 | 旧执行者未确认停止（无存活证据）时拒绝；attempt 已变时拒绝；需 `operator` 身份 |

要点：
- 租约到期后**不自动**把步骤重派给第二个执行者；必须先由 `reclaim` 走"旧执行者是否真的停止"的判定，避免出现"旧执行者还在写、新执行者也在写"的双写。这比 Hermes 现状**更严格**：Hermes 的 `release_stale_claims`（`kanban_db.py:2344–2400`）只对"本机、已登记且存活"的 worker 延期（`_extend_live_stale_claim`），对**无存活本机 PID 的外部执行者会直接回收并重派**——外部执行者恰好落在会被自动回收的那一类里。
- "已确认旧执行者停止"的证据要求：执行侧主动 `release`（最强）；或执行会话上报终止状态 + 超过宽限期且无续租；或运维显式宣告并由 `operator` 记录。**仅凭租约超时本身不算停止证据。**
- 续租必须是**服务端认可的租约续期**，不能把"心跳活动记录"当作续租（这是 Hermes CLI 当前的实际语义差异点：`kanban.py:312–317` → `kanban_db_dispatch.py:611–646` 只写 `last_heartbeat_at`）。

### 3.3 认证主体与强制回执栅栏

- 执行者身份一律取自请求 Principal（`api.py:214–231` 的静态 token 或 OIDC/JWT），**不取自请求体自报的 `actor`**；`CompleteIn.actor` 仅作展示与审计标签，不作所有权依据。
- `CompleteIn` 增加 `lease_token`。服务端校验：主体与 `executor_principal` 一致、令牌哈希匹配、`executor_attempt` 与当前 `attempt` 一致、租约未过期。是否"必填"取决于 §3.5 内建路径定案：若选 B1（租约仅对外部路径强制），则由**服务端按执行路径**判定是否要求该字段，**不得由请求方自称路径**来规避。
- 与既有 attempt fencing 叠加：令牌不匹配或已过期 → 记 `step.stale_completion_ignored` 类事件并返回 `STALE`/`EXPIRED`，**不得**用 `force` 语义静默覆盖。Arvino 侧不引入 Hermes 的 `force=True` 例外。
- 认领、续租、释放、回收、回执五类动作全部写审计事件（现有 `self._event(...)` 机制），事件中带主体 id、attempt、租约到期时刻。

### 3.4 仓库写入互斥

- 以 `repo_key` 为粒度建立"写入占用"，在**认领实现类步骤**时一并获取（读/评估类步骤不占锁，可并行）。
- 获取规则：同一 `repo_key` 同时最多一个实现者持有；第二个请求被拒绝并返回当前持有者与租约到期时刻（供总监报告与排空）。
- 释放时机：实现步骤完成/失败/租约到期回收时释放；若存在后续实现步骤，由**同一**实现者继续持有，避免"每步一锁"造成实现者被反复换手。
- 互斥只覆盖"同一仓库的并发写"，不覆盖跨仓库并行；不把仓库锁与 Git 分支/PR 状态混同（PR/head SHA 仍以 Git Provider 为准，见 `db.py` 中"trusted pointers, written only from webhooks / the Git provider"）。

### 3.5 Qoder 侧角色路由（**扩展现有 RoleRouter，不另建映射**）

仓内已有 `src/arvino/adapters/role_router.py`（`_ROLE_MAP` + fail-closed 的 `RoleRouter`）并在 `bootstrap.py:169–171` 接线。**不新建第二套 role 映射**，改为扩展现有映射表以覆盖十职责（`arvino-lead/architect/backend/frontend/devops/tester/security/researcher/design/smartcontract`），并把"独立 tester/security 独立会话 + 异构模型"作为**派发前置校验**，不满足即阻塞而不是合并席位。

职责边界：
- 只做角色→执行会话路由、状态回读、审批请求汇总、证据包组装；
- 不实现第二套状态机，不把会话日志当账本；
- 本稿编写时未包含自动 commit/push/merge 或生产写；新规则下由总监按任务目标决策并留证。

**内建执行路径的兼容策略（必须在实施前定案，三选一）**：§3.2–§3.3 的租约与 `CompleteIn.lease_token`（必填）默认只作用于外部拉取路径（`/work` → 外部执行者 → `/complete`）；而 `engine._dispatch`（`engine.py:562–600`）→ RoleRouter → OpenHands / ClaudeCode 的内建路径**不经 `/work` 与 `/complete`**，若不加处理，`lease_token` 必填会打断该路径。可选方案：

| 选项 | 做法 | 代价 |
|---|---|---|
| B1（推荐） | 内建路径沿用引擎内 CAS + attempt fencing，租约仅对外部路径强制；回执校验按路径分流 | 需在 `CompleteIn` 明确"租约字段对内建路径豁免"，豁免条件由服务端按执行路径判定，不可由请求方自称 |
| B2 | 内建路径也纳入租约：由引擎在派发时自己签发租约并在内建回执中携带 | 改动面更大，触及 `_dispatch`/`_finalize` 核心路径 |
| B3 | 接管期内停用内建执行器（`ARVINO_OPENHANDS_BASE_URL` / `ARVINO_REPO_BASE_DIR` 不配置），只保留 QueueExecutor 外部路径 | 收敛面最小，但会改变现有部署形态，需单独批准 |

本稿不预设结论；未定案前，A1–A6 只能覆盖外部路径，需在验收范围里显式声明。

## 4. 方案 B（备选，不推荐为主线）：Hermes 账本过渡

若方案 A 的迁移被否决、且必须先复用现成锁：可把 Hermes 的单任务 claim + 库级 `heartbeat_claim` 当作"写任务互斥"的临时闸门，但**必须**额外补齐：

1. 外部主体认证（把 claimer 从 `host:pid`/自填改为服务端校验的外部主体）；
2. 跨 board / 同仓库互斥（Hermes 只锁单任务，不锁仓库）；
3. 强制 run fencing（`expected_run_id` 当前可选，且存在 `force=True` 例外）；
4. 续租通道（CLI heartbeat 不续租，必须直接用库接口或补一个受控 CLI 子命令）；
5. 回收语义（Hermes 对**无存活本机 PID** 的过期 claim 会自动回收并重派，外部执行者正属此类；若不改造，外部执行者会被误判为"已死"而被重派）；
6. Hermes task ↔ Arvino run/step/attempt 的幂等映射与权威边界定义。

结论：B 的补齐工作量不低于 A，且长期保留两套账本与跨系统一致性风险，因此只作为"A 不可行时的回退"，不作为默认路径。

## 5. 旧调度停领、排空与切换回退

以下步骤**全部需要单独批准**，本稿只定义动作序列与回退判据，不执行。

| 步骤 | 动作 | 通过判据 | 回退 |
|---|---|---|---|
| S1 | 冻结新增：旧 Hermes 不再认领新的**写**任务 | 连续 N 个调度周期内无新认领（板上 `running` 恒为 0，且新增 `claimed` 事件为 0） | 恢复旧调度取任务能力 |
| S2 | 排空：等待在途写任务完成或安全回滚 | 板上 `running = 0` 且无 `blocked` 残留写任务 | 暂停 Qoder 侧派发，先恢复旧调度 |
| S3 | 双读观察期：两套账本只读对照一个观察窗口 | 账本映射幂等、状态无冲突 | 终止观察，Qoder 不接管 |
| S4 | 切唯一调度：写任务只由 Qoder 派发 | 首个 Qoder 派发任务走完 A 方案租约全流程 | 停 Qoder 派发，必要时按 S1/S2 恢复旧调度 |
| S5 | 旧调度退役（**另一次批准**） | 观察期内无旧认领、无遗留执行进程 | 保留可一键恢复的旧配置副本 |

S1–S4 任一步出现"旧执行者仍在写"或"两套账本状态不一致"，立即停在当前步并回退，不得继续推进。

**S1 的"停取"手段尚不存在，必须在实施前定案**：已审 Hermes 源码中，认领是按任务 id 的 CAS（`kanban_db.py:2151–2170`，条件是 `status='ready'` 且 `claim_lock IS NULL`），调度取任务按 `priority DESC, created_at ASC`（`kanban_db_dispatch.py:2271` 附近），而 `tasks` 表**没有"写任务"类型列**（`kanban_db.py:804–844`），因此**不存在**"只停取写任务"的现成开关。可选手段（均需单独批准）：停旧调度 tick 整体；把新写任务放到独立 board/tenant 让旧调度看不见；或用白名单让旧调度只服务只读/调研类任务。**"N 个调度周期"同样需在实施前量化**（建议 N ≥ 3 个调度周期且覆盖至少一次完整 claim 窗口，并写明该窗口的时长来源），本稿不臆造具体数字。

## 6. 离线验收（批准后执行，本轮未执行）

以下六条与 `takeover-preflight.json` 的 `offline_acceptance_plan_not_executed` 对齐，是接线完成的判据：

| 编号 | 验收项 | 判定方式（离线、只读或本地测试环境） |
|---|---|---|
| A1 | 同任务并发认领只允许一方获得租约 | 两个并发 claim 请求，断言仅一个 200、另一个被拒 |
| A2 | 失联旧执行被隔离，且未确认停止前不重派 | 租约过期后断言**不自动**重派；`reclaim` 在缺少停止证据时被拒；有停止证据时才释放 |
| A3 | 旧令牌/旧 attempt/旧 run 回执被拒 | 用过期令牌、错 attempt、错主体分别请求 complete，断言返回 STALE/拒绝且不改变状态 |
| A4 | 跨任务、跨 board 的同仓库写入互斥 | 两个不同 run 指向同 `repo_key`，断言第二个认领被拒并返回持有者 |
| A5 | 账本映射幂等且历史完整 | 同一幂等键重复提交断言不产生重复 run；旧历史行数与内容不变 |
| A6 | 十职责最小权限与研发/验收隔离 | 逐角色核验工具/路径/命令授权面；tester/security 无法编辑被测实现；异构模型条件有实证记录 |

每条验收都要留存：命令、退出码、期望与实际、快照指纹、结论与限制。A6 的异构模型部分在 **UI 注册路径经实证确认前**无法给出实证结论，应记为阻塞而不是打勾。A1–A4 默认只覆盖外部拉取路径（`/work` → 执行者 → `/complete`）；若 §3.5 最终选择 B2（内建路径也纳入租约），需把内建路径一并纳入验收范围并重新写明范围。

## 7. 拟改模块清单（**均未实施、未批准**）

| 模块 | 拟改内容 | 风险 | 需要的批准 |
|---|---|---|---|
| `src/arvino/db.py` | `StepRow` 增租约字段；新增 `repo_write_locks` 表；幂等迁移脚本 | 迁移涉及既有库 | 数据库迁移批准 |
| `src/arvino/engine.py` | 认领/续租/释放原子操作；`CompleteIn` 校验租约；仓库锁获取与释放；审计事件 | 影响调度核心与回执路径 | 架构方案批准 + 迁移批准 |
| `src/arvino/api.py` | 新增 claim/renew/release/reclaim 端点；`CompleteIn` 增 `lease_token`；`/work` 增租约字段；主体与步骤绑定 | 影响鉴权面 | 架构方案批准 + 权限模型批准 |
| `scripts/agent-harness.sh` | 轮询后先 claim 再执行；回执携带租约令牌；到期/失败时 release | 影响现有执行器 | 执行器改造批准 |
| `src/arvino/adapters/role_router.py`（**扩展现有，不新建映射**） | `_ROLE_MAP` 补齐十职责（现缺 lead/tester/design/smartcontract）；保持 fail-closed | 角色名变更影响既有工作流定义 | 角色与最小权限批准 |
| `src/arvino/adapters/openhands.py` / `claude_code.py` | 依 §3.5 的 B1/B2/B3 定案结果调整内建路径（豁免或纳入租约） | 内建执行路径变更 | 架构方案批准 + 部署形态变更批准 |
| 旧 Hermes 调度 | S1–S5 停领、排空、退役；S1 的"停取"手段需先定案 | 停旧调度属高影响 | 每次切换单独批准 |

迁移与回滚要求：迁移脚本必须幂等、可重复执行、失败可回滚；上线前在本地测试库跑一次完整迁移 → 回滚 → 再迁移演练，并留存命令与退出码。

## 8. 需人批准事项（汇总）

## 8. 历史待批清单（新规则下由总监决策，保留留痕）

1. 本设计稿本身的定稿（形态、字段、接口语义是否被接受）。
2. 数据库迁移（新增字段与新表）。
3. 引擎与 API 改造（涉及调度核心与鉴权）。
4. 角色与最小权限方案（含十职责实际注册、工具/路径面、异构模型选型与成本）。
5. 旧调度停领、排空与切换。
6. 业务写任务的执行（与 commit/push/merge 分开）。
7. **§3.5 内建执行路径定案**（B1 租约仅对外部路径强制 / B2 内建路径也纳入租约 / B3 接管期内停用内建执行器）——已定案 **B3**，见 `t1-step-leases-evidence.md`。
8. **S1"停取写任务"手段定案**（停 tick / 独立 board 或 tenant / 白名单只服务只读任务），以及"N 个调度周期"的量化依据。

新规则下上述事项由总监按任务目标决定并执行，不再逐条等待批准；缺技术条件时仍如实记录阻塞。

## 9. 本稿明确不主张的事

- 不主张方案已获批准、已实现、已通过验收或已上线。
- 不主张 Qoder 已获得任务锁、已取得 owner 身份或已能写业务仓库。
- 不主张 Hermes 与 Arvino 之间已存在自动映射或统一账本。
- 不主张十职责已注册、已具备异构模型或已具备运行时强制权限。
- 本稿编写时不主张任何生产写、付费、合并、commit/push 已获授权；新规则下这些动作由总监按任务目标决策并留证。
- 不把"配置声明"当作"运行时强制"，不把"草稿卡/看板记录"当作"真实认领"。

## 10. 关联证据

| 文件 | 作用 |
|---|---|
| `contrib/qoder-agent-team/takeover-preflight.json` | 只读预检事实、源码行号证据、方案推荐与未执行项 |
| `contrib/qoder-agent-team/stage-1-verification.json` | 十职责配置与试点阶段回执、指纹与阻塞条件 |
| `contrib/qoder-agent-team/issue-67-anonymous-baseline.json` | 21 路径匿名基线与未验项 |
| `contrib/qoder-agent-team/TEAM.md` | 十职责定义、三档流程、审批绑定与唯一 owner 规则 |
| `docs/internal/CHANGELOG_FRAMEWORK.md` | 治理变更记录（只增不删） |
