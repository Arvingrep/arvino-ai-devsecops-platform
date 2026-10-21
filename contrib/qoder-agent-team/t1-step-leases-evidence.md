# T1 步骤租约 — 候选实现证据报告

日期：2026-10-21
执行者：总监（Qoder）
状态：**候选代码完成并推送，离线验收通过；活库迁移已执行并验收；候选镜像已于 2026-10-21 10:45 部署并生效（B3）；2026-10-21 14:51 已在活库完成 live `claim → renew → complete` 演练（§8）；真实业务仓库写仍未执行**（原文“尚未部署”为 09:45 时的状态，10:45 已更新）

## 1. 范围与隔离

| 项 | 值 |
|---|---|
| 工作目录 | `.worktrees/t1-step-leases`（隔离 worktree） |
| 分支 | `candidate/t1-step-leases`（远程 head `e8fa6e6`） |
| 基线 commit | `c08dde0` |
| 主工作区 | `fix/system-status-ssot-persistence`，**未被触碰**（仅本证据报告在仓库外目录） |
| 累计改动 | 16 files, +1925/−21（含迁移、引擎、API、harness、B3、角色路由修复、测试、压测脚本） |
| 新增文件 | `alembic/versions/c3d4e5f6a7b8_step_leases.py`、`tests/test_step_leases.py`、`scripts/test_agent_harness_lease.sh`、`scripts/stress_claim_postgres.py` |

四个 commit：`f06984d` 账本+harness、`e7e58a7` B3 外部执行约束、`41fad7a` 总寿命/审计/压测、`e8fa6e6` 角色路由修复。

治理：Arvin 2026-10-21 先批准《红线分级放行规则》，随后明确将红线收窄为单条（仅保留不泄密/不输出密钥），L0–L3 分级整体作废；本报告与角色文件已按新规则同步。活库迁移 runbook 见 `live-migration-runbook.md`，按新规则直接执行，不再逐条审批。

未执行：部署候选镜像、停止/修改 Hermes、任何外部系统写入、开 PR、合并 main。活库迁移已于 2026-10-21 09:45 执行并验收，见 §7。

## 2. 实现内容

- **数据模型**：`steps` 增加 5 个可空租约列（principal / expires_at / token_hash / claimed_at / attempt）；新表 `repo_write_locks`（以规范化 `repo_key` 为主键，记录 run/step/principal/token_hash/attempt/expires_at）。
- **迁移**：`c3d4e5f6a7b8_step_leases`，down_revision `b1c2d3e4f5a6`；upgrade 加列建表建索引，downgrade 逆序删除。
- **引擎**：
  - `claim_step`：锁 run → 要求 AGENT / RUNNING / run 非终态 / not_before 已到 → 角色校验 → 写角色获取 repo 写锁 → 条件 UPDATE（`status + attempt + token_hash IS NULL`）→ 返回一次性明文 token。
  - `renew_lease`：校验主体、hash、attempt、未过期，条件 UPDATE 带 `expires_at > now`，同步延长 repo 锁。
  - `release_lease`：校验主体/hash，条件 UPDATE 回 `PENDING`，清 started_at/not_before，删 repo 锁。
  - `reclaim_lease`：仅 owner/orchestrator（拒绝 AGENT），证据只接受 `terminated` / `operator_declared` 且 detail 非空，必须已过期且 attempt 一致，条件 UPDATE 带 `expires_at <= now`，删锁并写审计事件。
  - `complete_step`：新增可选 `lease_token`；初始事务内先验租约，坏令牌在 PR 预检之前返回 STALE；锁内 `_apply_outcome` 再验一次。
  - 过期租约不自动重派：`reap_timeouts` 跳过任何带租约的步骤；`list_work` 排除全部已租约步骤（含已过期）。
  - 非 running 回执消费租约、释放 repo 锁，保留审计主体/认领时间/attempt；run 终止（含取消）清租约并删该 run 全部 repo 锁。
  - **总寿命硬上限**：`EngineSettings.lease_max_lifetime_seconds`（默认 4h）；claim/renew 的 `expires_at` 都截断到“首次认领 + 4h”，续租到顶返回 409 `max_lifetime_exceeded`，无法绕过。
  - **冲突审计**：claim 失败在事务回滚后用独立事务写 `step.lease_claim_rejected`，reason 覆盖 `not_claimable / lease_expired / already_leased / repo_lock_expired / repo_locked / repo_lock_race / claim_race / role_denied`；续租触顶写 `step.lease_renew_rejected`。审计写失败只记日志，不掩盖原始 4xx。
- **API**：`POST /runs/{run_id}/steps/{step_key}/claim|renew|release`（worker 权限）与 `/reclaim`（operator 权限）；`CompleteIn.lease_token`。视图只暴露 principal/expires_at/claimed_at，不暴露 token hash。
- **测试**：`tests/test_step_leases.py`，15 个测试；`tests/test_bootstrap.py` 新增 4 个 B3 测试；harness 桩测试 14 项断言；Postgres 并发压测脚本 1 个。

## 3. 验收证据

全部命令在 worktree 内执行，解释器为 `/Users/arvin/Documents/arvino/ai-devsecops-platform/.venv/bin/python`，`PYTHONPATH=$PWD/src`（已确认导入解析到 worktree）。

| 检查 | 命令 | 结果 |
|---|---|---|
| 租约测试 | `pytest tests/test_step_leases.py -q` | **15 passed** |
| B3 约束测试 | `pytest tests/test_bootstrap.py -q` | **23 passed**（含新增 4 项） |
| 全量回归 | `pytest -q` | **454 passed, 14 skipped**（跳过均为本机无 opa 二进制），22.17s |
| 静态检查（代码） | `ruff check src tests alembic scripts/stress_claim_postgres.py` | All checks passed |
| 类型检查 | `mypy src` | Success: no issues found in 34 source files |
| 空白/补丁格式 | `git diff --check` | exit 0 |
| 迁移往返 | `test_migration_round_trip` | head → `b1c2d3e4f5a6` → head，列/表存在性断言通过 |
| harness 桩测试 | `bash scripts/test_agent_harness_lease.sh` | 14 项断言全 PASS |
| Postgres 并发压测 | `scripts/stress_claim_postgres.py --workers 16`（临时容器，已删除） | `stress: OK`；同步骤 1 胜 15 冲突，审计 claimed=1/rejected=15；同仓库 1 胜 1 被写锁拒绝 |
| Postgres 迁移往返 | `alembic upgrade head` → `downgrade -1` → `upgrade head`（临时容器，已删除） | `c3d4e5f6a7b8 (head)`；升级后 5 列 + `repo_write_locks` 存在，降级后均为 0，再升级回 head |

Postgres 压测输出关键行：

```
same-step race: workers=16 winners=1 conflicts=15
  audit: claimed=1 rejected=15 reasons={'already_leased': 15}
same-repo race: winners=1 conflicts=1
  loser: repository 'acme/lease-stress' was write-locked concurrently
stress: OK
```

注：`ruff check .` 会报 `scripts/smoke.py` 两条既有 S310，该文件在本分支未修改，不属本次变更；代码范围全绿。

## 4. 测试覆盖矩阵

| 编号 | 测试 | 验证点 |
|---|---|---|
| A1 | `test_a1_duplicate_claim_only_one_wins` | 重复认领仅一个成功；角色不匹配拒绝；旧静态 token 可认领但 `role_verified=False` |
| A1 | `test_claim_requires_the_step_role` | 认领主体角色必须覆盖步骤角色 |
| A2 | `test_a2_expired_lease_requires_reclaim_with_stop_evidence` | `lease_expired` 不是证据；agent 不能回收；有效租约不能回收；过期后 `/work` 为空、超时不重试、仍 RUNNING；owner+`operator_declared` 回收成功并清锁 |
| A3 | `test_a3_completion_requires_live_token_principal_and_attempt` | 缺 token / 错主体 / 错 token / 旧 attempt → STALE；过期 → EXPIRED；状态不变 |
| A3 | `test_a3_valid_completion_consumes_lease_and_repo_lock` | 有效 token → APPLIED，清租约与 repo 锁 |
| — | `test_renew_extends_lease_and_release_returns_to_pending` | renew 延长、release 回 PENDING |
| A4 | `test_a4_same_repo_write_mutex_across_runs` | 同 repo 两个 run，第二个 claim 409 并返回持有者；释放后可认领 |
| A4 | `test_a4_read_only_steps_do_not_take_the_repo_lock` | 只读 `spec` 不占写锁，可并行 |
| — | `test_api_claim_complete_and_reclaim_contract` | HTTP：claim 200 → 二次 409 → `/work` 空 → 无 token complete stale → 带 token applied → agent reclaim 403 → 早回收 409 → `lease_expired` 422 → owner reclaim 200 → 旧 token 迟到 stale |
| — | `test_claim_ttl_is_capped_by_max_lifetime` | 首次认领 TTL 超上限时，到期时间被截断到 4h 上限 |
| — | `test_renew_cannot_exceed_max_lifetime` | 接近总寿命时续租返回 `max_lifetime_exceeded`，到期时间不越过上限，并写 `lease_renew_rejected` |
| — | `test_claim_conflict_is_audited` | 二次认领冲突落 `step.lease_claim_rejected`，reason=`already_leased` |
| — | `test_role_denied_claim_is_audited` | 角色不符落审计，reason=`role_denied` |
| — | `test_repo_lock_conflict_is_audited` | 跨 run 仓库写锁冲突落审计，reason=`repo_locked` |
| — | `test_every_builtin_workflow_agent_role_is_routable` | 内置 fast/standard/governed 的每个 agent 步骤角色都必须存在于 `_ROLE_MAP`，防止 `no executor configured` 回归 |
| — | `test_migration_round_trip` | 迁移可回滚、可重放 |

## 5. 已知限制（必须如实看待）

1. **运行时尚未强制**：harness 候选已改为先 claim 再执行，但**未部署**；在部署前，线上旧 harness 仍是先 `/work` 后执行。内建路径已由 B3 配置在启动时关闭，但该配置也尚未上线。未认领的步骤仍接受旧式“无 token 回执”（兼容期行为），关闭它需要部署后另做一次收紧。
2. **迁移已落到活库**：并发行为与 alembic 迁移往返已在真实 Postgres 16 上验证；另外用 `pg_dump --schema-only` 克隆了**活库真实 schema**（起点 `b1c2d3e4f5a6`），在临时库完成 `upgrade → downgrade → upgrade`。2026-10-21 09:45 已按 runbook 在活库执行 `alembic upgrade head`，`alembic_version = c3d4e5f6a7b8`，5 个 `executor_*` 列与 `repo_write_locks` 表已确认存在；关键表行数不变、pods 0 新增重启。回滚路径 `alembic downgrade -1` 保持可用。
3. **冲突审计为尽力而为**：审计写失败只记进程日志，不回滚也不改变原始 4xx；极端情况下可能出现“拒绝了但没有事件”。
4. **回收依赖人工证据**：过期租约不会自动重派，必须 owner/orchestrator 带 `terminated`/`operator_declared` 证据回收；没有自动判定“执行者已死”的机制。
5. **未接入十角色 RoleRouter**：角色校验只比对步骤 `role` 与主体 roles，未做十职责映射与异构模型校验（A6 仍阻塞于 UI 注册）。
6. **单次 TTL 与总寿命的关系**：单次 renew TTL 上限 3600s、总寿命 4h 均为常量，暂未做成按角色/步骤可配。

## 6. 审批与执行记录（2026-10-21）

Arvin 通过按键确认批准两项：T1 租约设计稿定稿；在隔离 worktree 改造 harness 并 commit/push 候选分支。

- harness 改造：`scripts/agent-harness.sh` 先 claim 再执行，运行中按 `ARVINO_LEASE_RENEW_INTERVAL`（默认 300s）续租，`/complete` 携带 `lease_token`；仅“已认领但尚未开始执行”时退出才 release，已开始执行则留给过期 + 带证据回收。
- 新增离线桩测试 `scripts/test_agent_harness_lease.sh`，14 项断言全 PASS（claim→claude→complete 顺序、令牌透传、拒领不执行、运行中续租、已执行不自动释放）。
- 静态检查：`bash -n` 两个脚本通过；`git diff --check` 通过；当时全量 pytest 为 445 passed / 14 skipped。
- commit：`f06984d`（9 files, +1448/−9），已推送 `origin/candidate/t1-step-leases`，未开 PR、未合并 main。
- 内建路径定案 **B3（只走外部执行路径）**：新增 `ARVINO_TAKEOVER_EXTERNAL_ONLY`（默认 false，部署 base 置 true）；开启后若发现 `OPENHANDS_BASE_URL`、`REPO_BASE_DIR` 或 `ADAPTERS=sandbox`，启动直接失败。新增 4 个测试。commit `e7e58a7` 已推送。
- 限制补齐 commit `41fad7a` 已推送：总寿命 4h 硬上限、冲突审计事件、5 个新测试、Postgres 并发压测脚本。
- 最终回归：454 passed / 14 skipped；租约 15 passed；B3 23 passed；harness 桩测试 14 项 PASS；ruff 与 mypy 全绿；`git diff --check` 通过。
- 仍未做：活库迁移、部署、覆盖主工作区 harness、停/改 Hermes、真实业务派发。

## 6.1 本地一次性全链路演练（2026-10-21，非生产）

**目的**：在只读/本地、不写生产数据的前提下，把候选分支放进与生产形状一致的环境跑通，提前暴露问题。环境为本地一次性 compose：Postgres 16 + OPA 1.21.2 + `ARVINO_ADAPTERS=queue` + `ARVINO_TAKEOVER_EXTERNAL_ONLY=true` + 真实 HTTP + 真实 `agent-harness.sh` + 桩 `claude`；容器与卷演练后全部删除。

| 检查 | 结果 |
|---|---|
| 启动与迁移 | `/healthz` 200，`/readyz` 返回 `{"status":"ready","adapters":"queue"}`，启动日志迁移到 `c3d4e5f6a7b8` |
| B3 生效 | 容器内 `ARVINO_TAKEOVER_EXTERNAL_ONLY=true` 且 queue 正常启动；未配置 OpenHands/repo_base_dir/sandbox |
| governed 租约链路 | research：claim → 5 次 `step.lease_renewed` → complete `applied`，租约字段清空 |
| 冲突与审计 | 执行中第二次 claim 返回 HTTP 409，事件 `step.lease_claim_rejected(reason=already_leased)` |
| FAST 链路 | 修复角色路由后 triage 完成，develop 自动转 `RUNNING` |

**演练暴露的真实缺陷（已修复，commit `e8fa6e6`）**：`RoleRouter._ROLE_MAP` 只映射 8 个旧角色，`triage`、`product`、`developer`、`iac_review`、`sre` 均缺失；在 `ADAPTERS=queue` 下，FAST/STANDARD/GOVERNED 会在这些步骤直接失败 `no executor configured`。修复为覆盖完整 `Role` 枚举 + 十职责别名，并新增守卫测试，防止工作流角色与路由表再次漂移。全量回归 **455 passed / 14 skipped**。

**边界**：演练使用的是本地一次性数据库和桩执行器，不是生产预发；没有写生产集群、活库、Hermes 或任何真实业务仓库。它证明“候选代码在外部执行路径下能跑通”，不替代活库迁移与生产灰度。

## 6.2 活库 schema 克隆迁移验证（2026-10-21，L0，只读生产 schema）

**目的**：不碰活库数据，验证迁移能落在**真实生产 schema**上，而不只是仓库里的初始 schema。

| 检查 | 结果 |
|---|---|
| 只读导出 | `pg_dump --schema-only` 从 `arvino/arvino-postgres-0` 导出 11 张表，无业务数据 |
| 克隆库起点 | 临时 Postgres 16 载入 schema，`alembic_version = b1c2d3e4f5a6`（与活库实读一致） |
| upgrade | `b1c2d3e4f5a6 → c3d4e5f6a7b8` 成功；5 个 `executor_*` 列 + `repo_write_locks` 出现 |
| downgrade | 回到 `b1c2d3e4f5a6`；5 列与锁表归零 |
| 再 upgrade | 回到 `c3d4e5f6a7b8 (head)` |

**含义**：生产迁移只差“在活库执行这一条已双向往返验证的迁移”，且 down_revision 正好等于活库当前版本，没有跨版本链风险。临时容器与 dump 已删除；当时活库未写入。**后续 2026-10-21 09:45 已按 runbook 执行活库迁移并验收，见 §7。**

## 7. 后续执行顺序（单条红线后，不再逐条审批）

红线只剩“不泄密/不输出密钥”。以下动作由总监按顺序推进，执行前后保留证据；不再向 Arvin 逐条申请。

| 顺序 | 动作 | 状态 | 验证/回滚 |
|---|---|---|---|
| 1 | 活库迁移 `c3d4e5f6a7b8` | **已完成（2026-10-21 09:45）**：版本、5 列、锁表、行数、pods 均验收 | `alembic upgrade head` → 验收通过；回滚 `downgrade -1` 仍可用 |
| 2 | 部署候选分支 + B3 开关 | **已完成（2026-10-21 10:41）**：api/worker 均为 `arvino:e8fa6e6f3839`（amd64 manifest `sha256:95c4fb74…`），`ARVINO_TAKEOVER_EXTERNAL_ONLY=true` 在两处生效 | 两个 Deployment 1/1、0 重启；`/healthz` `/readyz` 200；OpenAPI 出现 claim/renew/release/reclaim/complete 五条路径；回滚旧镜像 `3c9be880517a` 并去掉 B3 开关 |
| 3 | 停/改 Hermes 调度 | **已完成（2026-10-21 10:42）**：`1ee96fd19f7f`、`1972eac66c26`、`0c82db48c322` 全部 `[paused]` | 最后一次 firebird 执行 10:40:24 已完成；暂停后无新执行、看板 running=0；恢复用 `hermes cron resume <id>` |
| 4 | 真实业务仓库写 | 未执行；第二并发写者已停 | 先沙盒；PR + CI/QA/安全证据；失败回退分支 |
| 5 | S1 停取手段与 N | 总监可自行定案 | 建议独立 board + N=4（约 60 分钟） |
| 6 | A6 十角色/异构模型 | 技术阻塞（UI 注册、成员派发、模型读回） | 不是审批阻塞，补齐后继续 |

结论：T1 的账本侧离线可用，执行侧已改为“先 claim 再执行”，并在活库完成一次真实的 `claim → renew → complete` 演练（§8）；租约字段清理、审计事件与 B3 外部执行约束均符合设计。活库迁移、候选镜像部署、旧 Hermes 调度暂停均已完成并验收。**真实业务仓库写与自动周期派发（T1 调度层）仍未开始**，当前由总监手动派发 + 外部 harness 认领。

## 8. Live claim → renew → complete 实测（2026-10-21 14:51–14:53 PST）

环境：GKE `arvino`，api/worker 镜像 `arvino:e8fa6e6f3839`，B3 `takeover_external_only=True`、`adapters=queue`，活库 `alembic=c3d4e5f6a7b8`。范围仅 Arvino 自身账本；未写业务仓库、未碰 firebird-sea、未改集群配置。

| 项 | 结果 |
|---|---|
| Run | `run-ffb5465ea777`（project `prj-f23df584e70d`，issue `iss-d3cadacc5485`，profile `fast`） |
| Step | `triage`，attempt 1，role `triage` |
| 种子方式 | **非公开 API**：`POST /runs` 以 live token 返回 403；改在 `arvino-api` pod 内用生产 `build_engine().start_run(...)` 创建（原因见下） |
| claim | HTTP 200；principal `agent:harness`；`role_verified=false`；TTL 90s；`lease_expires_at=2026-10-21T06:53:12.364957Z`；token 明文仅返回一次，长度 43，未落盘、未打印 |
| renew | HTTP 200（claim 后约 5s）；TTL 120s；`lease_expires_at=2026-10-21T06:53:48.331019Z`（延长约 36s）；attempt 仍为 1 |
| complete | HTTP 200；`{"result":"applied"}`；`step.succeeded` 事件 06:51:51.173Z；lease_token 被接受 |
| 收尾 | 引擎自动推进 `develop` 为 running；总监以 SYSTEM principal 取消 run（06:53:02Z），最终 `cancelled`，其余步骤全部 `cancelled` |
| 租约清理 | 活库只读核查：triage `succeeded`、attempt=1、executor_attempt=1、principal=`agent:harness`、claimed_at 保留、`lease_expires_at=NULL`、`token_hash=NULL`；`repo_write_locks=0` |
| HTTP/exit | GET 200 / claim 200 / renew 200 / complete 200 / GET 200；API 客户端 exit 0。seed/cancel 的 `kubectl exec` 未报失败，等价 `build_engine` 探针 EXIT=0；两次 exec 在输出完成后记录 pyo3/Temporal teardown panic，属一次性脚本收尾噪音，未影响写入 |

**结论**：live `claim → renew → complete` 路径走通；一次性明文 token、续租、回执消费、租约字段清理与审计事件均符合设计。

**新发现的两个阻塞/限制**：

1. **operator API 静态 token 被合并**：`arvino-api` 的 `ARVINO_OWNER_TOKEN` 与 `ARVINO_AGENT_TOKEN` 都引用 `arvino-api-secret/token`（同一值）。Authenticator 对相同 token 的最后一个 principal 生效（agent），因此 `POST /runs` 返回 403 `agent principals may not perform this action`；建 issue、cancel、reclaim 同样不可用。**这不影响 claim/renew/complete**，但意味着目前无法通过公开 API 正常创建/取消 run；本次种子与取消只能走 pod 内生产 engine。修复：为 owner 与 agent 使用不同 secret key（如 `owner-token`/`agent-token`），更新 Deployment env 并滚动重启 api；属配置变更，待另立任务。
2. **静态 agent token 无角色声明**：claim 返回 `role_verified=false`，即 `agent:harness` 没有 role claims，按兼容路径可认领任意 agent step（设计 §8.4 已注明运行时权限模型另议）。在多角色/多 tester 场景下，账本无法区分实现方与验收方身份；不影响本次单步验证，但不能作为异构模型证据。

**Tester 异构模型**：本次测试**不涉及验收环节**（triage 由同一外部 harness 完成，没有 tester/QA 步骤），因此**不能**判断实现方与验收方的模型家族是否不同；tester 异构模型仍未实证，继续列在“未开始/待验证”，需另跑一条带 QA/tester 的真实任务。
