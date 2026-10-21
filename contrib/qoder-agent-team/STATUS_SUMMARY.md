# Arvino 接管进度 · 一页速览

> **更新：2026-10-21 20:05 PST** · 维护：Qoder 总监值班
> 这是**当前快照**，不是历史日志；有实质进展时覆盖更新。
> **范围**：本文件只覆盖 Arvino 调度接管项目，与接管无关的业务应用变更（如 Teams bot、其他仓库的其他 PR）不记录在此；这类事项属于各自的线，PR 该审还审、该合还合，但不写进这份速览。
> 技术细节与逐轮证据：[SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md) · [CHANGELOG_FRAMEWORK.md](../../docs/internal/CHANGELOG_FRAMEWORK.md)

**一句话**：旧调度已停、只读巡检已接管并连续运行；租约闭环、owner operator API、GitOps 收敛、Telegram 通道、**T1 账本心跳**都已上线并有实证；剩下的是几个只有你能做的动作，和尚未部署的外部 harness。

## 需要 Arvin 动手（按优先级）

1. **在 Qoder 设置页重贴十个角色的工作指令**（仓库文件已改，UI 不会自动同步）。
2. **决定 11 个 blocked 看板任务**：补凭据、恢复执行，还是取消；并指定 firebird PR 审核由谁接。
3. **考虑提高 Qoder 当日用量上限**：账号日配额今天 16:05 用尽过（新会话一律 403 `Billing daily count exceeded`），巡检已改模型续命，明天同一时点可能再触顶。
4. **建议把平台仓库转为 private**：仓库现为 public，台账含内网主机名、ArgoCD 应用名、chat id 与本地路径（**无任何密钥**），建议转私有或把台账迁到私有库。

## 进行中

- **PR 队列（仅接管相关）**：接管分支 #29（台账与 contrib 全量入库）已合并；两个与接管相关的 open PR 按「有问题不合并」只记录原因——#28（总监守卫生效方式与现行单条红线治理冲突）、#23（harness 令牌变量改名已被主线超越），是否关闭或 rebase 由你定。
- **T1 派发执行侧**：心跳侧已自动上线，但 `GET /work` 的派发队列仍无消费者（外部 harness 未部署）；方案（改哪里/风险/回滚）见设计稿。→ [设计](automation-dispatch-design.md)
- **候选分支并入 main**：租约代码所在分支（`candidate/t1-step-leases`）尚未并入 main，合并前需处理 alembic 链与镜像更新（线上仍跑 `e8fa6e6f3839`）。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **Qoder 团队启用**：团队已注册（1 leader + 4 member），等 UI 指令重贴后跑一次必须由成员动手的任务。→ [注册核验](registration-verification.json)

## 已完成

- 旧 Hermes 三个 cron job 全部暂停；10:42 起看板 0 running、0 新认领。
- B3 接管模式上线：api/worker 运行 `e8fa6e6f3839`，`takeover_external_only=True`、`adapters=queue`。
- 活库迁移 `c3d4e5f6a7b8` 已执行：`executor_*` 5 列 + `repo_write_locks` 就位。
- **真实认领闭环已打通**：2026-10-21 14:51 live `run-ffb5465ea777` 的 `triage` 完成 claim→renew→complete（HTTP 200/200/200，token 未落盘，租约字段已清理）。→ [证据](t1-step-leases-evidence.md)
- **Operator API 已恢复**：owner/agent 拆成独立 secret key（`owner-token`/`token`，值不同，SealedSecret 已同步重封），api 滚动重启中断 59s、0 重启；owner 实测 `POST /runs` 201、`cancel` 200，agent 建 run 仍 403、读接口 200。→ [证据](t1-step-leases-evidence.md)
- **T1 账本心跳已上线生产集群**：CronJob `arvino-t1-heartbeat` 每 5 分钟自动做租约清单、派发可见性不变量与过期租约回收；本机 E2E（建 run→claim→过期→自动 reclaim→步骤回 pending）与集群验收均通过，生产 CronJob 已按 `*/5` 实际触发并成功完成。→ [证据](t1-step-leases-evidence.md)
- **命名空间 NetworkPolicy 纳入 GitOps**：原先只存在于集群的 5 条策略已收编，带外漂移消除。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **GitOps 已收敛**：homelab main `a996720` 同步 Deployment 引用、SealedSecret 双键与心跳清单；`arvino-orchestrator-gke` Synced。
- Telegram 提醒通道已纠正并验证：根因是 chat id 配错（旧 id 已失效，非连接器故障）；24h 提醒改发 Arvin 私聊、巡检异常告警改发 k8sopenclaw 运维群，两处试发均成功。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- 巡检 job 已迁至新会话（熔断根因是账号日配额 + auto 分类器 400），17:30 起恢复 15 分钟节奏。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- T1 租约离线验收通过：claim/renew/release/reclaim、token 哈希、4h 总寿命、冲突审计、16 路并发压测。
- 只读巡检已接管并持续运行（每 15 分钟；15:00 轮捕获 ArgoCD 漂移、17:30 轮恢复迁移后节奏；24h 提醒基线已建，下次到点约 10-22 11:20）。→ [指导书](status-inspection-prompt.md)
- 单条治理红线已写入仓库 13 份角色文件；旧五条红线与 L0–L3 分级作废。
- ArgoCD 全部 Application 均 Synced；仅已知陈旧状态位 Degraded 与 image-updater ComparisonError 例外。

## 未开始

- **外部 harness 部署**：自动派发的执行侧（真实跑步骤的进程）尚未部署；续租目前由执行者自持 token 自续。
- **受控试点（真实业务写）**：firebird-sea 的首个业务写试点仍待 PR 审核归属确定后启动。
- **十角色异构模型与成员派发实证**：Arvino 标准流虽有 `qa`/`security` agent 步骤，但目前由同一外部队列执行；Qoder 团队 tester 需 UI 指令重贴后才能接入并读回实际模型家族。

## 被阻塞

- **Qoder UI 指令**：需要你手工重贴，云端不会读仓库。
- **11 个看板任务**：调度已停，当前无执行者；最老已 112h，需逐项决定。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **firebird PR 审核 / 夜间流程**：原 Hermes job 已停，暂无接管者。

## 已知但不阻塞

- **推 homelab main 前必须核对本地 HEAD 指向**：该仓有自动同步应用，推 main 会直接生效；今天已因此误带一条无关变更并回退。
- ArgoCD `arvino-orchestrator-gke` 应用级 `Degraded` 是 2026-10-19 的陈旧状态位（24/24 资源 Synced 且无非 Healthy）。
- `gitea.k8shome.com` 525、审批 webhook 404、agent-canvas 仅本机 8080：均为已知基线；Gitea 网页 525 时 open PR 核查可能不可读，内网 git 推送仍正常。
- 巡检每 15 分钟产生一次 `[skip ci]` 台账提交（约 96 次/日）；嫌吵可降为 30 分钟。
- canary `/house` 偶发单次 curl 000、重试即 200：判为瞬时抖动，继续观察。

---

**更新规则**：只写当前状态，不写历史；每项一行，技术细节一律外链；状态变化才更新（无变化不动），并同步首行时间；**只写接管项目范围内的事项**。逐轮历史见 [SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md)。
