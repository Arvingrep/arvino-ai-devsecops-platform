# 接管方案级审批请求（Arvino 调度接管方案 v1）

> ## ⚠️ 2026-10-21 归档：红线已收窄为单条，本文件不再等待批复
>
> Arvin 已在本会话确认：红线只保留**不泄密/不输出密钥**；原五条红线、L0–L3 分级与逐项审批全部作废。**本文件以下内容仅保留决策留痕**，不构成当前流程；执行顺序以 `t1-step-leases-evidence.md` §7 与 `live-migration-runbook.md` 为准。总监按任务目标直接推进，不再逐条请示。
>
> ## ⚠️ 2026-10-21 09:44 状态校正：本文件部分内容已被并行实施取代，勿按旧文执行
>
> 本文件写于 2026-10-21 凌晨，当时接管尚未实施。此后 Hermes 侧在同一仓库并行推进，现状与本文件描述**已不一致**，逐条对照见下。**本文件降级为「仍开放项」的索引，不再是完整的方案审批请求。**
>
> | 本文件原表述 | 实际现状（2026-10-21 09:44 实测） |
> |---|---|
> | §7.1 待批 B1/B2/B3 | **已定案 B3**，commit `e7e58a7`（`ARVINO_TAKEOVER_EXTERNAL_ONLY`，接管期内只走外部执行路径） |
> | "引擎与 API 改造未实施" | **已实施**于候选分支 `candidate/t1-step-leases`（远程 head `e8fa6e6`）：`src/arvino/engine.py` +711、`api.py` +72、`db.py` +29、`scripts/agent-harness.sh` +125、迁移 `c3d4e5f6a7b8`（`steps` 加 5 租约列 + `repo_write_locks` 表） |
> | "A1–A6 全部未执行" | **A1–A4 已有测试与压测证据**：`tests/test_step_leases.py` 15 项 + `tests/test_bootstrap.py` B3 4 项 + `scripts/test_agent_harness_lease.sh` 14 断言 + Postgres 并发压测 16 worker 同步骤 1 胜 15 冲突；A5 随账本映射实现，A6 **仍阻塞于 UI 注册与异构模型实证** |
> | "活库迁移、部署、停旧调度仍未执行" | **已更正**：活库迁移 `c3d4e5f6a7b8` 已于 2026-10-21 09:40 执行并验收通过（`alembic_version` `b1c2d3e4f5a6`→`c3d4e5f6a7b8`；5 个可空租约列 + `repo_write_locks` 就位；关键表行数不变；pods 0 新增重启；`/healthz` 200；旧镜像未变）。**部署候选分支/B3 开关与停旧调度仍未执行** |
> | "红线不变（5 条逐条批准）" | **红线已收窄为 1 条**（仅不泄密/不输出密钥），见 `CHANGELOG_FRAMEWORK.md` 同日两条治理条目与 `redline-release-policy-proposal.md`；本文件原有的审批分级措辞随之作废 |
> | §7.2 S1 停取手段 / §7.3 N 量化 | **仍开放**，是 Hermes 侧列出的待批项，未见定案 |
> | §7.4 firebird 值守 job 并发写 | **仍开放，且因接管已接近切换而更紧要**——本项未被并行实施覆盖 |
>
> 仍开放项的权威清单以 `contrib/qoder-agent-team/t1-step-leases-evidence.md` §7 为准；本文件 §7.4 补充了那份清单没有覆盖的**第二个并发写者**。
>
> 状态：草稿，未提交。按 `approval-request-template.md` §0 与 §模板 B 起草，仅供 Arvin 在对话中审阅。
> 未获 Arvin 明确回复前，不提交到 GitHub / Telegram / n8n / 任何外部系统，也不据此实施任何改动。
> 一次批准只覆盖一次具体动作。**本文件原稿的"红线豁免"措辞（不花钱、不碰生产集群写操作、不输出密钥、不绕过 QA 门禁、不自己合并 PR）已被 2026-10-21 的红线收窄取代，仅"不泄密/不输出密钥"仍为红线**；该收窄已由 Arvin 在本会话明确确认，不再是待确认项。

---

## 模板 B：方案级审批请求

### 1. 方案与产物

| 项 | 值 |
|---|---|
| 名称 / 版本 | Arvino 调度接管方案 v1 |
| 产物绝对路径 | `/Users/arvin/Documents/arvino/ai-devsecops-platform/contrib/qoder-agent-team/takeover-design-proposal.md` |
| 产物 sha256 | `e40003ae3489e6442c2f7ff8a9f5bc77a53c4026508d9e1644f2c71aed2efde7` |
| 支撑证据 1 | `contrib/qoder-agent-team/takeover-preflight.json`（只读预检）sha256 `9aa722f2c4a01742b459810b7ee222fc15d00bd7d2baeb80899c26563551da27` |
| 支撑证据 2 | `contrib/qoder-agent-team/takeover-design-review.json`（独立静态审查回执，10 条意见已全部处理）sha256 `9d7fffe51223895f8a0bec72f8365a48f477bdaa50017756987d5e8e581b62ff` |
| 支撑证据 3 | `contrib/qoder-agent-team/registration-verification-checklist.md`（十角色注册核验清单）sha256 `856c1c887bd98e41e1c35dd62fd7453463240b08d251c2fd163afb8fb959a556` |
| 支撑证据 4 | `contrib/qoder-agent-team/registration-playbook.md`（注册操作卡，本轮新增）sha256 `22b59170522d299903f002422eaf35ce9e57229c2f5bf68f1f552042764c8229` |
| 支撑证据 5 | `contrib/qoder-agent-team/approval-request-template.md`（本模板）sha256 `610f962ffbcac9d8d96c53d4deb163c1887f96513f6a1fc21b538a7a5df14e00` |
| 支撑证据 6 | `contrib/qoder-agent-team/TEAM.md` sha256 `cfb4beffa1a6017e07603800031744a3f7c4df6163480146a9599235c3728b88`；`roles.yaml` sha256 `50df6ad7dc3728b4ccf4d0dc0dd0227184106d81db290212416a76092b773f58` |
| 支撑证据 7 | `contrib/qoder-agent-team/registration-verification.json`（**注册核验回执，2026-10-21 结论 `passed_with_blockers`**）sha256 `f961e03dbf133965bad36cebaaef69328eae5be4a852817af54d996039d6dc82` |
| 角色索引 | `contrib/qoder-agent-team/roles.yaml`（`deployment_status: prepared_not_registered`） |

sha256 为记录时点的实测值（`shasum -a 256`）；本目录文件在 2026-10-21 上午存在并行编辑，后续取值以磁盘实测为准，差异登记在 `docs/internal/CHANGELOG_FRAMEWORK.md` 的「记录修正」行。

### 2. 方案要解决什么 / 不解决什么

**解决**

1. Qoder 作为唯一调度者后，"同一任务不会被两个执行者同时认领"、"同一仓库不会被两个实现者同时写"有可执行契约与验收判据。
2. 回执必须来自持有租约的执行者；旧令牌 / 旧 attempt / 旧 run 的回执被拒。
3. 旧调度如何停领、排空、退役，每一步有明确动作、通过判据与回退。

**不解决**

1. ~~十角色在 Qoder UI 的实际注册~~ → **已于 2026-10-21 完成首批五个席位**（lead/backend/frontend/devops/tester），证据见 `registration-verification.json`；其余五个职责仍未注册。
2. 异构模型的实证 → **仍无实证**。UI 注册已完成，但 tester 与 security 均未执行过，没有实际运行模型记录；且一次真实团队任务只产生了 leader 会话，四名成员未被派发为独立执行会话。
3. 旧业务任务的历史回填。
4. 生产写、合并、付费——新规则下由总监按任务目标决策执行，不再是动作级审批。

### 3. 关键取舍

- **选定**：方案 A —— 复用 Arvino 既有持久化账本、事务/CAS/attempt 隔离与 CI/OPA/GitOps 门禁，新增"外部执行者租约"维度。
- **不选**：新建第三套账本；或以 Hermes 看板账本为主线做过渡。
- **理由**：Arvino 侧已有事务边界、attempt fencing、审批绑定版本与门禁；Hermes 侧要补齐外部主体认证、强制 run fencing、仓库锁与跨账本幂等映射，工作量不低于方案 A，且长期保留两套事实源（与"唯一真相源"冲突）。
- **备选路径**：若方案 A 的迁移被否决，才退到方案 B（Hermes claim 作临时闸门），且必须同时补齐其 6 项缺口，不作为默认路径。

### 4. 拟改动面（全部未实施）

| 模块 | 拟改内容 | 风险 | 需要的批准 |
|---|---|---|---|
| `src/arvino/db.py` | `StepRow` 增租约字段；新增 `repo_write_locks` 表；幂等迁移脚本 | 迁移涉及既有库 | 数据库迁移批准 |
| `src/arvino/engine.py` | 认领/续租/释放原子操作；`CompleteIn` 校验租约；仓库锁获取与释放；审计事件 | 调度核心与回执路径 | 架构方案 + 迁移 |
| `src/arvino/api.py` | 新增 claim/renew/release/reclaim 端点；`CompleteIn` 增 `lease_token`；`/work` 增租约字段；主体与步骤绑定 | 鉴权面 | 架构方案 + 权限模型 |
| `scripts/agent-harness.sh` | 轮询后先 claim 再执行；回执携带租约令牌；到期/失败 release | 影响现有执行器 | 执行器改造批准 |
| `src/arvino/adapters/role_router.py`（**扩展现有，不新建映射**） | `_ROLE_MAP` 补齐十职责（现缺 lead/tester/design/smartcontract）；保持 fail-closed | 角色名变更影响既有工作流定义 | 角色与最小权限批准 |
| `src/arvino/adapters/openhands.py` / `claude_code.py` | 依 §7.1 定案结果调整内建路径（豁免或纳入租约） | 内建执行路径变更 | 架构方案 + 部署形态变更 |
| 旧 Hermes 调度 | S1–S5 停领、排空、退役 | 高影响 | 每次切换单独批准 |

迁移要求：脚本必须幂等、可重复执行、失败可回滚；上线前在本地测试库跑一次"迁移 → 回滚 → 再迁移"演练，留存命令与退出码。

### 5. 验收判据（离线）

| 编号 | 验收项 | 判定方式 |
|---|---|---|
| A1 | 同任务并发认领只允许一方获租约 | 两个并发 claim，断言仅一个 200、另一个被拒 |
| A2 | 失联旧执行被隔离，未确认停止前不重派 | 租约过期后断言不自动重派；`reclaim` 缺停止证据时被拒 |
| A3 | 旧令牌/旧 attempt/旧 run 回执被拒 | 分别用过期令牌、错 attempt、错主体请求 complete，断言 STALE/拒绝且状态不变 |
| A4 | 跨任务、跨 board 的同仓库写入互斥 | 两个不同 run 指向同 `repo_key`，断言第二个认领被拒并返回持有者 |
| A5 | 账本映射幂等且历史完整 | 同一幂等键重复提交不产生重复 run；旧历史行数与内容不变 |
| A6 | 十职责最小权限与研发/验收隔离 | 逐角色核验工具/路径/命令授权面；tester/security 无法编辑被测实现；异构模型条件有实证记录 |

每条留存：命令、退出码、期望与实际、快照指纹、结论与限制。

> **A1–A4 默认只覆盖外部拉取路径**（`/work` → 执行者 → `/complete`）。若 §7.1 选 B2，需把内建路径纳入验收范围并重写范围声明。
> **A6 的异构模型部分在 UI 注册实证完成前无法给结论，记为阻塞，不打勾。**

### 6. 风险与回退

| 风险 | 缓解 | 回退 |
|---|---|---|
| 迁移失败或不可回滚 | 幂等脚本 + 本地"迁移→回滚→再迁移"演练留存退出码 | 回滚迁移，恢复原结构；此时 Qoder 不接管 |
| 租约到期但旧执行者仍在写 | A2 要求回收前必须有停止证据，缺证据即拒绝释放 | 停在当前步，人工确认旧执行已停 |
| 双调度 | S1–S4 分步放行，每步有"无新认领 / running=0"判据 | S1–S4 任一步出现旧执行仍在写或两账本不一致，立即停在当前步并回退 |
| 仓库并发写 | `repo_key` 粒度锁 + 唯一 owner 规则 | 释放持有者租约并冻结该 repo_key 的新认领 |
| 跨账本状态不一致 | S3 双读观察期 | 终止观察，Qoder 不接管 |

本文件原稿的“未获批准即阻塞”规则属于旧流程。当前除唯一红线外，动作由总监决策并留证：活库迁移已于 2026-10-21 09:45 执行并验收；候选镜像部署与停/改旧调度按 `t1-step-leases-evidence.md` §7 推进；firebird-sea 业务写仍受第二并发写者约束，停旧调度前保持阻塞。

### 7. 历史待批事项（新规则下不再逐条批复）

以下 4 项是**旧流程**下要 Arvin 逐条批复的内容，现仅保留决策留痕；B3 等已实施项不再等待批复，S1/N/停旧调度由总监按任务目标定案并执行。

#### 7.1 §3.5 内建执行路径定案（三选一）— **已于 2026-10-21 定案 B3，本项关闭，不再需要 Arvin 批**

> 实际执行：commit `e7e58a7 feat(takeover): enforce external-only execution (B3)`，新增 `ARVINO_TAKEOVER_EXTERNAL_ONLY`（默认 false，部署 base 置 true）；开启后若发现 `OPENHANDS_BASE_URL`、`REPO_BASE_DIR` 或 `ADAPTERS=sandbox`，启动直接失败。`tests/test_bootstrap.py` 新增 4 个 B3 测试（23 passed）。**部署该开关本身属 L1/L2，须单独批准。** 下表保留为决策留痕。

| 选项 | 做法 | 代价 |
|---|---|---|
| **B1（推荐）** | 内建路径沿用引擎内 CAS + attempt fencing，租约仅对外部路径强制；回执校验按执行路径分流 | 需在 `CompleteIn` 明确"租约字段对内建路径豁免"，豁免条件由服务端按执行路径判定，**不可由请求方自称** |
| B2 | 内建路径也纳入租约，由引擎在派发时自签租约并在内建回执中携带 | 改动面更大，触及 `_dispatch`/`_finalize` 核心路径 |
| B3 | 接管期内停用内建执行器（不配 `ARVINO_OPENHANDS_BASE_URL` / `ARVINO_REPO_BASE_DIR`），只保留外部路径 | 收敛面最小，但改变现有部署形态，需单独批准 |

**推荐 B1 的理由**：租约要解决的是"外部执行者被误判为已死而被重派"这个问题，内建路径在引擎进程内，不存在该风险；B2 为不存在的问题增加核心路径改动面，B3 直接改变部署形态。

> 批复锚点：`7.1 批 B1` / `7.1 批 B2` / `7.1 批 B3`

#### 7.2 S1"停取写任务"手段定案（三选一）— **推荐"独立 board/tenant"**

| 选项 | 做法 | 评估 |
|---|---|---|
| **独立 board/tenant（推荐）** | Qoder 的写任务放到新建独立 board，旧调度看不见 | **本轮实证支持**：全机 17 个 Hermes cron job 中，只有 `0c82db48c322`「arvino看板总监值守」调用 `hermes kanban dispatch`，其 prompt 硬编码 `hermes kanban boards switch arvino`；`/Users/arvin/.hermes/kanban/boards/` 下当前只有 `arvino` 一个 board。旧 job 显式切换到 `arvino`，不会自动扫到新 board。副作用最小，且不伤旧调度既有的只读值守与 `diagnostics` 职责 |
| 停旧 tick | 整体停 `0c82db48c322` | 最粗暴。该 job 同时承担看板总监值守与 `hermes kanban diagnostics` 卡住任务巡检，一停就丢了这部分既有价值 |
| 白名单只服务只读任务 | 让旧调度只接只读/调研类任务 | **当前不可行**：`tasks` 表没有"写任务"类型列（已审源码 `kanban_db.py:804–844`），无法在任务级区分读写。除非先加列，那就是一次数据库迁移 |

> 批复锚点：`7.2 批独立 board` / `7.2 批停 tick` / `7.2 批白名单`（选白名单的话请同时批准"先加任务类型列"这项迁移）

#### 7.3 "N 个调度周期"的量化 — **推荐 N = 4（约 60 分钟）**

本轮实证的周期数据：

| 数据 | 实测值 | 来源 |
|---|---|---|
| 旧 arvino 看板调度周期 | 15 分钟 | job `0c82db48c322` schedule = `interval / 15m` |
| Hermes cron ticker 基础间隔 | 60 秒 | `/Users/arvin/.hermes/hermes-agent/cron/jobs.py:99` `TICKER_INTERVAL_SECONDS = 60` |
| 任务认领默认 TTL | 900 秒 = 15 分钟 | `takeover-preflight.json` → `claim_default_ttl_seconds: 900` |
| 认领 CAS 条件 | `status='ready'` 且 `claim_lock IS NULL` | 已审源码 `kanban_db.py:2151–2170` |

推导：S1 的通过判据是"连续 N 个调度周期内无新认领（板上 running 恒为 0，新增 claimed 事件为 0）"。要让这个结论站得住，观察窗口必须**至少覆盖一个完整 claim 生命周期**，否则窗口内可能仍有未过期的旧认领存活。

- 最小：N ≥ 3 个调度周期 = 45 分钟（设计稿建议的下限）
- 推荐：**N = 4 个调度周期 = 60 分钟**，覆盖 4 倍 claim TTL 窗口（60 min ≫ 15 min），并且覆盖至少一次完整的认领-过期循环

> 批复锚点：`7.3 批 N=4（60 分钟）` / 或给出其他 N（需说明其与 900 秒 TTL 的覆盖关系）

#### 7.4 **新增**：firebird 值守 job 对 firebird-sea 的并发写处置

这是本轮新查到的证据，之前的设计稿与预检都未覆盖。

| job | 周期 | 行为 | 是否走 arvino 看板 |
|---|---|---|---|
| `1ee96fd19f7f`「firebird PR 审核与 BMAD 有序推进」 | **每 3 分钟** | 审核并 squash merge PR、删分支；必要时用 worktree + gates.sh 修复重推 | 否（走 GitHub Project V2 #3） |
| `1972eac66c26`「firebird 夜间流程推进」 | `30 23,2,5 * * *` | 清点在途 PR、合并、回写 sprint-status / issue 状态 | 否 |

**为什么必须单独定案**：Arvino 方案里的"同仓库写入互斥"锁在 Arvino 账本的 `repo_key` 粒度上，只能约束 Arvino 侧的认领者。而上面两个 job 直接在 firebird-sea 仓库上动手，既不经过 Arvino 账本、也不经过 Hermes 看板。因此当前 T0 巡检里"看板 `running=0` ⇒ 无双调度"的判断**覆盖不到它们**，用它来放行 Qoder 在 firebird-sea 上的写任务是不成立的。

**建议（三个选项，请择一或给替代方案）**：

1. **A（推荐）**：firebird-sea 写任务在两个值守 job 停领或收窄为只读之前**保持阻塞**；Qoder 团队先在本仓（ai-devsecops-platform）和只读范围内活动。
2. **B**：把两个 firebird 值守 job 一并纳入 S1 停领范围，与 arvino 看板停领同批放行、单独批准。
3. **C**：把两个值守 job 收窄为只读审核（可出结论、不合并不修复重推），写操作全部交给 Qoder 唯一调度——需要单独定义"只读审核"的判定与停工条件。

> 批复锚点：`7.4 批 A` / `7.4 批 B` / `7.4 批 C` / 或其他方案

### 8. 请求人 / 归档记录

| 项 | 值 |
|---|---|
| 请求人 | `arvino-lead`（Qoder 总监会话） |
| 请求时间 | 2026-10-21 |
| 提交状态 | **未提交；已归档**，仅保留决策留痕 |
| 归档原因 | 红线收窄为单条，逐项审批流程作废 |
| 后续执行 | 见 `t1-step-leases-evidence.md` §7 与 `live-migration-runbook.md` |

---

## 附：本次请求的未验证项（如实列出，不掩盖）

1. ~~十角色尚未在 Qoder UI 注册~~ → **已注册首批五个席位**，映射见 `roles.yaml` 的 `qoder_agent_profile_id`，回执见 `registration-verification.json`（结论 `passed_with_blockers`）。
2. 异构模型**仍无实证**：UI 层已确认支持逐智能体默认模型（文档级），但 tester 与 security 从未执行，无实际运行模型家族记录。硬条件未满足即不得勾选 A6。
3. **成员派发链路未观测**：2026-10-21 03:34 的真实团队任务只产生了 leader 一个会话，四名成员无独立执行会话，leader 自行完成了只读任务。"总监拆派给专业角色"这一核心链路在运行时是否成立，**尚未被证明**。
4. 逐智能体工具权限的实际值（尤其 tester/security 禁止列表含 Edit/Write）无法本地读回，需在设置页逐个读回并留证。
5. `hermes kanban boards` 的 board 创建 / 切换命令语义未实查；§7.2 推荐"独立 board"前需先核实该命令族确实隔离 board 视图。
6. ~~A1–A6 全部**未执行**~~ → **已更正**：A1–A4 已有离线测试与真实 Postgres 并发压测证据（见 `t1-step-leases-evidence.md` §3–§4）；A5 随账本映射实现；**A6 仍阻塞**（未接入十职责 RoleRouter 映射与异构模型校验，见该文件 §5.5）。**活库迁移、部署、停旧调度仍未执行**，不能宣称已接管派发。
7. 两个 firebird 值守 job 的实际执行范围以 cron prompt 为准，未观测其近期真实执行记录（其行为可能随 prompt 演进而变）。
8. 本轮**未**认领、派发、完成、关闭任何任务；**未**改旧调度；**未**运行迁移；**未**做业务写、付费、commit/push/merge。
