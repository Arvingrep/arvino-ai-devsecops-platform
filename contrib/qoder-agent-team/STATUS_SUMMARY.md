# Arvino 接管进度 · 一页速览

> **更新：2026-10-21 15:04 PST** · 维护：Qoder 总监值班
> 这是**当前快照**，不是历史日志；有实质进展时覆盖更新。
> 技术细节与逐轮证据：[SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md) · [CHANGELOG_FRAMEWORK.md](../../docs/internal/CHANGELOG_FRAMEWORK.md)

**一句话**：旧调度已停、只读巡检已接管、B3/租约/真实认领闭环都已跑通；新发现 api Deployment 带外漂移且 Telegram 提醒通道不可达，叠加 operator token 配置等人工动作待办。

## 需要 Arvin 动手（按优先级）

1. **排查 `Deployment/arvino-api` 带外改动**：巡检 15:00 发现 ArgoCD `arvino-orchestrator-gke` 由 Synced 转 OutOfSync（git main@f075222c82 未变），疑与 PR#222 所述注入 GH_TOKEN 的带外修改相关；确认来源后决定 git 侧收敛或集群侧回正。
2. **修复 hermes Telegram 提醒通道**：`hermes send --to telegram:-1004348153434` 报 Chat not found（2 次），阻塞异常告警与 10-22 起的 24h blocked 提醒送达。
3. **吊销并重新签发泄漏的 `GH_TOKEN`**（GitHub 侧，旧值仍有效），随后合并 PR#222/#219 改为 `secretKeyRef`。
4. **在 Qoder 设置页重贴十个角色的工作指令**（仓库文件已改，UI 不会自动同步）。
5. **决定 11 个 blocked 看板任务**：补凭据、恢复执行，还是取消；并指定 firebird PR 审核由谁接。

## 进行中

- **PR 队列**：PR#29（本分支）已扩到全量 contrib + 台账，需按新内容重审后合并；另有 4 个 open PR 排队。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **Qoder 团队启用**：团队已注册（1 leader + 4 member），等 UI 指令重贴后跑一次必须由成员动手的任务。→ [注册核验](registration-verification.json)

## 已完成

- 旧 Hermes 三个 cron job 全部暂停；10:42 起看板 0 running、0 新认领。
- B3 接管模式上线：api/worker 运行 `e8fa6e6f3839`，`takeover_external_only=True`、`adapters=queue`。
- 活库迁移 `c3d4e5f6a7b8` 已执行：`executor_*` 5 列 + `repo_write_locks` 就位。
- **真实认领闭环已打通**：2026-10-21 14:51 live `run-ffb5465ea777` 的 `triage` 完成 claim→renew→complete（HTTP 200/200/200，token 未落盘，租约字段已清理）；run 已取消收尾（因 operator token 阻塞，run 由 pod 内 engine 种子）。→ [证据](t1-step-leases-evidence.md)
- T1 租约离线验收通过：claim/renew/release/reclaim、token 哈希、4h 总寿命、冲突审计、16 路并发压测。
- 只读巡检已接管并持续运行（每 15 分钟；连续多轮无退化，15:00 轮首次捕获 ArgoCD 漂移；24h 提醒基线已建，下次到点约 10-22 11:20）。→ [指导书](status-inspection-prompt.md)
- 单条治理红线已写入仓库 13 份角色文件；旧五条红线与 L0–L3 分级作废。
- 除 `arvino-orchestrator-gke`（见「需要 Arvin 动手」#1）外，ArgoCD 其余 Application 均 Synced/Healthy；仅已知陈旧状态位与 image-updater ComparisonError 例外。

## 未开始

- **T1 账本心跳 / 自动派发**：真实认领闭环已跑通，可以开始接线；尚未动手。→ [设计](automation-dispatch-design.md)
- **firebird-sea 业务写试点**：等 PR 审核归属确定。
- **外部 harness 部署**：等 T1 稳定后再做。
- **十角色异构模型与成员派发实证**：等 UI 重贴后补；本次 lease 测试没有验收环节，不能作为 tester 异构模型证据。

## 被阻塞

- **GH_TOKEN 轮换**：只有 Arvin 能在 GitHub 侧操作；在此之前泄漏值仍有效。→ [告警](../../docs/internal/SYSTEM_STATUS.md)
- **operator API 被 token 配置阻塞**：owner 与 agent 共用同一 secret key，静态 token 一律认证为 agent；`POST /runs`、建 issue、cancel/reclaim 均 403。需拆分 owner/agent 两个 secret key 并滚动重启 api（总监/devops 可执行，约 1 分钟中断）；不影响 claim/renew/complete。→ [证据](t1-step-leases-evidence.md)
- **Qoder UI 指令**：需要 Arvin 手工重贴，云端不会读仓库。
- **11 个看板任务**：调度已停，当前无执行者；最老已 107h，需逐项决定。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **firebird PR 审核 / 夜间流程**：原 Hermes job 已停，暂无接管者。
- **PR#29 合并**：不是故障，等一次独立重审后合并。

## 已知但不阻塞

- ArgoCD `arvino-orchestrator-gke` 应用级 `Degraded` 是 2026-10-19 的陈旧状态位（15:00 轮 `lastTransitionTime` 未变）；同轮新出现的 `Deployment/arvino-api` OutOfSync 已升级为待办 #1，不属于此项。
- `gitea.k8shome.com` 525、审批 webhook 404、agent-canvas 仅本机 8080：均为已知基线。
- 巡检每 15 分钟产生一次 `[skip ci]` 台账提交（约 96 次/日）；嫌吵可降为 30 分钟。
- canary `/house` 偶发单次 curl 000、重试即 200：判为瞬时抖动，继续观察。

---

**更新规则**：只写当前状态，不写历史；每项一行，技术细节一律外链；状态变化才更新（无变化不动），并同步首行时间。逐轮历史见 [SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md)。
