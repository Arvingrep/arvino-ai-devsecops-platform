# Arvino 接管进度 · 一页速览

> **更新：2026-10-21 19:55 PST** · 维护：Qoder 总监值班
> 这是**当前快照**，不是历史日志；有实质进展时覆盖更新。
> 技术细节与逐轮证据：[SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md) · [CHANGELOG_FRAMEWORK.md](../../docs/internal/CHANGELOG_FRAMEWORK.md)

**一句话**：旧调度已停、只读巡检已接管并连续运行；租约闭环、owner operator API、GitOps 收敛、Telegram 通道、**T1 账本心跳**都已上线并有实证；剩下的是几个只有你能做的动作，和尚未部署的外部 harness。

## 需要 Arvin 动手（按优先级）

1. **Gitea homelab #224（hermes-feishu 换 Teams bot）的 4 项前置**：`teams login`、`teams app create` 取 CLIENT_ID/SECRET/TENANT_ID、terraform apply 新 DNS 记录、手工注入 `hermes-feishu-secrets`。未完成前不能合并（该应用是自动同步，合并即部署没有凭据的 Teams 通道）。
2. **在 Qoder 设置页重贴十个角色的工作指令**（仓库文件已改，UI 不会自动同步）。
3. **决定 11 个 blocked 看板任务**：补凭据、恢复执行，还是取消；并指定 firebird PR 审核由谁接。
4. **考虑提高 Qoder 当日用量上限**：账号日配额今天 16:05 用尽过（新会话一律 403 `Billing daily count exceeded`），巡检已改模型续命，明天同一时点可能再触顶。
5. **建议把平台仓库转为 private**：仓库现为 public，台账含内网主机名、ArgoCD 应用名、chat id 与本地路径（**无任何密钥**），建议转私有或把台账迁到私有库。

## 进行中

- **PR 队列**：GitHub 本轮合并 3 个（#26 `GET /events`、#29 台账全量入库、#25 移除 Plane 集成并修掉 alembic 修订号重号），剩 2 个不合并（#28 治理冲突、#23 已过时）；Gitea #224 待前置完成后可合并。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **T1 派发执行侧**：心跳侧已自动上线，但 `GET /work` 的派发队列仍无消费者（外部 harness 未部署）；方案（改哪里/风险/回滚）见设计稿。→ [设计](automation-dispatch-design.md)
- **Qoder 团队启用**：团队已注册（1 leader + 4 member），等 UI 指令重贴后跑一次必须由成员动手的任务。→ [注册核验](registration-verification.json)

## 已完成

- 旧 Hermes 三个 cron job 全部暂停；10:42 起看板 0 running、0 新认领。
- B3 接管模式上线：api/worker 运行 `e8fa6e6f3839`，`takeover_external_only=True`、`adapters=queue`。
- 活库迁移 `c3d4e5f6a7b8` 已执行：`executor_*` 5 列 + `repo_write_locks` 就位。
- **真实认领闭环已打通**：2026-10-21 14:51 live `run-ffb5465ea777` 的 `triage` 完成 claim→renew→complete（HTTP 200/200/200，token 未落盘，租约字段已清理）。→ [证据](t1-step-leases-evidence.md)
- **Operator API 已恢复**：owner/agent 拆成独立 secret key（`owner-token`/`token`，值不同，SealedSecret 已同步重封），api 滚动重启中断 59s、0 重启；owner 实测 `POST /runs` 201、`cancel` 200，agent 建 run 仍 403、读接口 200。→ [证据](t1-step-leases-evidence.md)
- **T1 账本心跳已上线生产集群**：CronJob `arvino-t1-heartbeat` 每 5 分钟自动做租约清单、派发可见性不变量与过期租约回收；本机 E2E（建 run→claim→过期→自动 reclaim→步骤回 pending）与集群验收均通过。→ [证据](t1-step-leases-evidence.md)
- **命名空间 NetworkPolicy 纳入 GitOps**：原先只存在于集群的 5 条策略已收编，带外漂移消除。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **GitOps 已收敛**：homelab main `a996720` 同步 Deployment 引用、SealedSecret 双键与心跳清单；`arvino-orchestrator-gke` Synced。
- **PR#29/#26/#25 已合并入 main**（台账全量入库防分支切换丢失、`GET /events` 全局 feed、移除 Plane 集成）；应用代码运行态仍未变：api/worker 镜像仍 `e8fa6e6f3839`（清单 revision 已前移，镜像未跟随），尚未部署。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- Telegram 提醒通道已纠正并验证：根因是 chat id 配错（旧 id 已失效，非连接器故障）；24h 提醒改发 Arvin 私聊、巡检异常告警改发 k8sopenclaw 运维群，两处试发均成功。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- 巡检 job 已迁至新会话（熔断根因是账号日配额 + auto 分类器 400），17:30 起恢复 15 分钟节奏。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- T1 租约离线验收通过：claim/renew/release/reclaim、token 哈希、4h 总寿命、冲突审计、16 路并发压测。
- 只读巡检已接管并持续运行（每 15 分钟；15:00 轮捕获 ArgoCD 漂移、17:30 轮恢复迁移后节奏；24h 提醒基线已建，下次到点约 10-22 11:20）。→ [指导书](status-inspection-prompt.md)
- 单条治理红线已写入仓库 13 份角色文件；旧五条红线与 L0–L3 分级作废。
- ArgoCD 全部 Application 均 Synced；仅已知陈旧状态位 Degraded 与 image-updater ComparisonError 例外。

## 未开始

- **外部 harness 部署**：自动派发的执行侧（真实跑步骤的进程）尚未部署；续租目前由执行者自持 token 自续。
- **firebird-sea 业务写试点**：等 PR 审核归属确定。
- **十角色异构模型与成员派发实证**：Arvino 标准流虽有 `qa`/`security` agent 步骤，但目前由同一外部队列执行；Qoder 团队 tester 需 UI 指令重贴后才能接入并读回实际模型家族。

## 被阻塞

- **Gitea #224**：等你的 4 项前置。
- **Qoder UI 指令**：需要你手工重贴，云端不会读仓库。
- **11 个看板任务**：调度已停，当前无执行者；最老已 112h，需逐项决定。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **firebird PR 审核 / 夜间流程**：原 Hermes job 已停，暂无接管者。
- **PR#28 / #23**：#28 与现行单条红线治理冲突（把「总监不得下场」写死成硬阻断）、#23 改动已被 main 超越；两者按「有问题不合并」只记录原因、保持 open，是否关闭或 rebase 由你定。

## 已知但不阻塞

- **homelab main 推送即自动部署**：`hermes-feishu`/`hermes-feishu-gke` 是 automated+selfHeal+prune，推 main 会直接生效；今天已因此误带一条未就绪变更并回退，推送前必须核对本地 HEAD 指向。
- ArgoCD `arvino-orchestrator-gke` 应用级 `Degraded` 是 2026-10-19 的陈旧状态位（17/17 资源 Synced 且无非 Healthy）。
- `gitea.k8shome.com` 525、审批 webhook 404、agent-canvas 仅本机 8080：均为已知基线；Gitea 网页 525 时 open PR 核查可能不可读，内网 git 推送仍正常。
- 巡检每 15 分钟产生一次 `[skip ci]` 台账提交（约 96 次/日）；嫌吵可降为 30 分钟。
- canary `/house` 偶发单次 curl 000、重试即 200：判为瞬时抖动，继续观察。

---

**更新规则**：只写当前状态，不写历史；每项一行，技术细节一律外链；状态变化才更新（无变化不动），并同步首行时间。逐轮历史见 [SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md)。
