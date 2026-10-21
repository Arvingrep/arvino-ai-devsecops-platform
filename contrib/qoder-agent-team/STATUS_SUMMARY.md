# Arvino 接管进度 · 一页速览

> **更新：2026-10-21 19:30 PST** · 维护：Qoder 总监值班
> 这是**当前快照**，不是历史日志；有实质进展时覆盖更新。
> 技术细节与逐轮证据：[SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md) · [CHANGELOG_FRAMEWORK.md](../../docs/internal/CHANGELOG_FRAMEWORK.md)

**一句话**：旧调度已停、只读巡检已接管并恢复连续运行（巡检 job 已迁至 Full Access 新会话），B3/租约/真实认领闭环、owner operator API、GitOps 收敛与 Telegram 提醒通道都已跑通；GitHub 本轮合并 3 个 PR（代码已入库、运行态未变），现在只剩 Qoder UI 指令、PR#28 rebase 和看板遗留任务等人工动作。

## 需要 Arvin 动手（按优先级）

1. **在 Qoder 设置页重贴十个角色的工作指令**（仓库文件已改，UI 不会自动同步）。
2. **决定 11 个 blocked 看板任务**：补凭据、恢复执行，还是取消；并指定 firebird PR 审核由谁接。
3. **考虑提高 Qoder 当日用量上限**：账号日配额在今天 16:05 用尽（新会话一律 403 `Billing daily count exceeded`），巡检已改用当前会话模型续命，但明天同一时点可能再次触顶。
4. **PR#28 需 rebase**：base 分支前移后转为 CONFLICTING，重基后才能评审合并。

## 进行中

- **PR 队列**：GitHub 本轮合并 3 个（#26 GET /events、#29 台账全量入库、#25 移除 Plane 集成，`main`→`2244995`），open 降至 2：#23 可合并、#28 有冲突待 rebase；Gitea homelab 新增 #224（hermes-feishu 换 Teams bot，可合并）待评审。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **Qoder 团队启用**：团队已注册（1 leader + 4 member），等 UI 指令重贴后跑一次必须由成员动手的任务。→ [注册核验](registration-verification.json)

## 已完成

- 旧 Hermes 三个 cron job 全部暂停；10:42 起看板 0 running、0 新认领。
- B3 接管模式上线：api/worker 运行 `e8fa6e6f3839`，`takeover_external_only=True`、`adapters=queue`。
- 活库迁移 `c3d4e5f6a7b8` 已执行：`executor_*` 5 列 + `repo_write_locks` 就位。
- **真实认领闭环已打通**：2026-10-21 14:51 live `run-ffb5465ea777` 的 `triage` 完成 claim→renew→complete（HTTP 200/200/200，token 未落盘，租约字段已清理）；run 已取消收尾。→ [证据](t1-step-leases-evidence.md)
- **Operator API 已恢复**：owner/agent 拆成独立 secret key（`owner-token`/`token`，值不同，SealedSecret 已同步重封），api 滚动重启中断 59s、0 重启；owner 实测 `POST /runs` 201（`run-7067ae89b67d`）、`cancel` 200，agent 建 run 仍 403、读接口 200。→ [证据](t1-step-leases-evidence.md)
- **GitOps 已收敛**：homelab main `88b3203` 同步 Deployment 引用与 SealedSecret；`arvino-orchestrator-gke` 已恢复 Synced（本次变更所致，不是未知带外改动）。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **Telegram 提醒通道已纠正并验证**：根因是 chat id 配错（旧 id 已失效，非连接器故障）；24h 提醒改发 Arvin 私聊、巡检异常告警改发 k8sopenclaw 运维群，两处试发均成功。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **巡检 job 已迁至新会话**：熔断恢复后仍无成功轮次（16:02→17:30 断档 88 分钟），根因是会话创建阶段失败（旧 job 模型桶配额 403 `Billing daily count exceeded`、auto 审批分类器 400）；已迁至 Full Access 新会话 `bd0998dd`，17:30 轮成功。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **PR#29/#26/#25 已合并入 main**（#29 台账全量入库防分支切换丢失、#26 GET /events 全局 feed、#25 移除 Plane 集成）；运行态未变：镜像仍 `e8fa6e6f3839`、ArgoCD revision 仍 `88b3203`，尚未部署。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- T1 租约离线验收通过：claim/renew/release/reclaim、token 哈希、4h 总寿命、冲突审计、16 路并发压测。
- 只读巡检已接管并持续运行（每 15 分钟；15:00 轮捕获 ArgoCD 漂移、15:15 轮确认恢复 Synced；17:30 轮为迁移后首轮成功，24h 提醒基线已建，下次到点约 10-22 11:20）。→ [指导书](status-inspection-prompt.md)
- 单条治理红线已写入仓库 13 份角色文件；旧五条红线与 L0–L3 分级作废。
- ArgoCD 全部 Application 均 Synced；仅已知陈旧状态位 Degraded 与 image-updater ComparisonError 例外。

## 未开始

- **T1 账本心跳 / 自动派发**：真实认领闭环已跑通，可以开始接线；尚未动手。→ [设计](automation-dispatch-design.md)
- **firebird-sea 业务写试点**：等 PR 审核归属确定。
- **外部 harness 部署**：等 T1 稳定后再做。
- **十角色异构模型与成员派发实证**：Arvino 标准流虽有 `qa`/`security` agent 步骤，但目前由同一外部队列执行；Qoder 团队 tester 需 UI 指令重贴后才能接入并读回实际模型家族。

## 被阻塞

- **Qoder UI 指令**：需要 Arvin 手工重贴，云端不会读仓库。
- **11 个看板任务**：调度已停，当前无执行者；最老已 109h，需逐项决定。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **firebird PR 审核 / 夜间流程**：原 Hermes job 已停，暂无接管者。
- **PR#28 冲突**：合并 #25/#26/#29 后 base 前移，PR#28 变为 CONFLICTING/DIRTY，需 rebase 后再评审。

## 已知但不阻塞

- ArgoCD `arvino-orchestrator-gke` 应用级 `Degraded` 是 2026-10-19 的陈旧状态位（15:15 轮 `lastTransitionTime` 仍未变、15/15 资源 Synced 且无非 Healthy）。
- `gitea.k8shome.com` 525、审批 webhook 404、agent-canvas 仅本机 8080：均为已知基线；Gitea 网页 525 时 open PR 核查可能不可读，内网 git 推送仍正常。
- 巡检每 15 分钟产生一次 `[skip ci]` 台账提交（约 96 次/日）；嫌吵可降为 30 分钟。
- canary `/house` 偶发单次 curl 000、重试即 200：判为瞬时抖动，继续观察。
- 巡检 job 的失败都在会话创建阶段（模型桶配额 403 / 审批分类器 400），非巡检逻辑问题；已迁至 Full Access 新会话，若再熔断需改换模型/执行位置或改为手动兜底。

---

**更新规则**：只写当前状态，不写历史；每项一行，技术细节一律外链；状态变化才更新（无变化不动），并同步首行时间。逐轮历史见 [SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md)。
