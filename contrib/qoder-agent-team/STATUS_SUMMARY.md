# Arvino 接管进度 · 一页速览

> **更新：2026-10-22 14:00 PST** · 维护：Qoder 总监值班
> 这是**当前快照**，不是历史日志；有实质进展时覆盖更新。
> **范围**：本文件只覆盖 Arvino 调度接管项目，与接管无关的业务应用变更（如 Teams bot、其他仓库的其他 PR）不记录在此；这类事项属于各自的线，PR 该审还审、该合还合，但不写进这份速览。
> 目录索引：[README.md](README.md) · 技术细节与逐轮证据：[SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md) · [CHANGELOG_FRAMEWORK.md](../../docs/internal/CHANGELOG_FRAMEWORK.md)

**一句话**：旧调度已停、只读巡检已接管（昨夜整段未运行 12.5 小时，本轮已恢复）；租约闭环、owner operator API、GitOps 收敛、Telegram 通道、**T1 账本心跳**都已上线并有实证；**团队派发通道已验证 4 轮仍未通——你新建的团队任务里协作工具仍未挂载（0 个已连接 MCP 服务器），四个成员仍零会话、5 条任务一条未派出**；剩下的是几个只有你能做的动作，和尚未部署的外部 harness；**新增：10-22 05:57Z 起 Temporal 已恢复——`temporal-postgresql-0` 在 Pending 115 分钟后调度成功，temporal 7/7 Running、history/matching 崩溃循环停止；容器日志实证崩溃的直接根因是 **DB 不可达**（`no usable database connection found`），此前「全集群 request 超卖为根因」的表述据此修正（超卖是并发条件、DB 不可达才是直接因），原先要你定的容量处置方向随之作废**；**新增待观察：GKE 节点池 web 升级（节点镜像滚动）引发工作负载反复重建，n8n 截至 14:00 仍 Pending/503（已发运维群告警），Arvino 自身 4/4 全绿、5 个端点均 200**；**另：给 Qoder 的支持询问已于 10-22 13:46 发出，等回复**。

## 交接给团队任务（推进到此为止）

- **重要背景**：到 2026-10-21 20:57 为止，本项目的推进方式是**总监与 Arvin 的单聊对话**，**没有**通过「Arvino 研发交付组」的团队任务机制。因此 backend / frontend / devops / tester **从未被真正派发过**，十角色的团队执行链一次都没跑过——此前所有结论都来自总监单体的实操与实测，不代表团队派发链可用。
- **接续方式**：后续工作一律以**团队任务**方式开启（选 Arvino 研发交付组），并先在 Qoder 设置页重贴十角色工作指令（仓库文件已改好，UI 不会自动同步）。
- **首次真团队派发验证（2026-10-21 21:04 起，累计 4 轮，结果仍未通过）**：宿主**确实注入了**团队上下文（5 名成员 + 实时 `discussionAgentMemberId`）与 `send_discussion_agent_message` 能力声明，但**该工具在会话工具面未挂载**（MCP 侧注册 0 个 server，`mcp_get` 全部 not found，运行日志 `Connected servers: 0`）→ 给 arvino-devops 的只读探针**未派发成功**（状态=未派发；总监未自答、未用本地 subagent 冒充成员会话）。**21:10 客户端重启后复验：仍是 0 工具**（重启后新出现的 5 个 run 经 argv 核实是插件自检，非成员会话）。**21:22 你手开的新团队会话里第 4 轮复验：仍是 0 工具、0 成员会话**，根因收敛为「协作 MCP 服务器未注册」。→ [过程证据](archive/dispatch-verification-2026-10-21.md)
- **必读 4 件**：`README.md`（目录索引）→ 本文件（速览）→ `docs/internal/SYSTEM_STATUS.md`（台账，只增不删）→ `automation-dispatch-design.md`（心跳/派发设计与 §12 下一步方案）。
- **已在生产运行**：巡检 job `bd0998dd`（15 分钟，Full Access）、心跳 CronJob `arvino-t1-heartbeat`（每 5 分钟）、租约 claim/renew/release/reclaim/complete、owner operator API、GitOps 已收敛、Telegram 通道已纠正。
- **尚未开始/待定**：① 第一个真实任务（哪个仓库、要达成什么）待 Arvin 指定——外部 harness 接线的验收对象就是它；② 外部 harness 接线（`scripts/agent-harness.sh` 现成，本机 `claude` CLI 2.1.291 与 `CLAUDE_CODE_OAUTH_TOKEN` 均就绪，缺的是接上租约协议的 claim→execute→renew→complete）；③ 候选分支（租约代码）并入 main + alembic 合并迁移 + 镜像 tag 手动跟进；④ 十角色异构模型与成员派发实证。
- **写给接手团队的坑**：账号日配额 10-21 16:05 用尽过（新会话全被 403 拒绝，巡检因此熔断 88 分钟）；**10-21 22:30→10-22 11:02 巡检又整段未运行 751 分钟（零轮次、零提交，原因未在本地判定）**；homelab main 推送即自动部署（10-21 曾误带一条无关变更并回退）；平台仓库是 public，台账含内网拓扑但**无任何密钥**。

## 需要 Arvin 动手（按优先级）

> **最高优先｜这就是「权益」卡点：项目/讨论限企业版，你的是个人版**。Qoder 官方文档「项目」原文写着 **「适用版本：企业版（Teams、Enterprise）。个人版暂不支持。」**（`docs.qoder.com/zh/qoder/projects`）；而智能体团队派发就靠「讨论」这个功能——个人版没有它 → 派发工具**根本不会挂载**，所以派不出去。本机实测与官方表述严丝合缝：套餐请求 HTTP **200 成功**（非网络/鉴权/陈旧会话），返回 **`userType:"personal_professional_trial"`、`isPersonalVersion:true`、`hasOrganization:false`、`isHighestTier:false`**，且该值在 **168 次采样中恒定不变**；代码侧 `hasOrganization` 由"组织字段非 null"推出 ⇒ 本机组织字段为 null（个人账号）。**"登出后重新登录"大概率无效**（那只是应用通用提示文案），仅作 1 分钟低成本复核。**进展：询问邮件已按你的指示于 2026-10-22 13:46 发出**——收件人 `support@qoder.com`（取自客户端包内唯一官方支持地址），附完整复现步骤、日志对照与 6 个待确认问题；发件用的是本机 Mail.app 里**唯一配置的 iCloud 账号**（未配置你的 Qoder 登录邮箱，也**未新增/未改动任何邮箱账号、未碰任何凭据**），正文已写明你的 Qoder 登录账号以便对方在账号侧定位。**现等 Qoder 回复**。**动作**：① 等回复（若要自己跟进，材料见 [询问材料](archive/qoder-support-request-2026-10-22.md)，仓库内副本保留了邮箱占位符）；② 若确需企业版，则为该账号开通或加入 **企业版（Teams / Enterprise）**；③ **权益通了还要新开一条团队任务**——本会话工具面创建时已固定，不会中途长出工具。**验证三条需同时成立**：启动日志出现 `Project Discussion access initialization completed {"enabled":true}`；应用日志 `MCP session injection prepared` 的 `finalNames` 非空集；`mcp_list` 非空。→ [证据 §11–§14](archive/dispatch-verification-2026-10-21.md)

1. **确认 n8n 是否随节点池升级完成而恢复（2026-10-22 14:00 新增）**：`n8n` Deployment **0/1 ready**、Pod `n8n-7c6dffd4ff-dx4ns` 仍 **Pending/Unschedulable**，`https://n8n.k8shome.com/healthz` 返回 **503**（基线 200）。成因是 **GKE 节点池 web 正在升级**（节点镜像 `gke.1439001`→`gke.1626001`，节点被反复替换/驱逐，autoscaler 仍报 `2 max node group size reached`）。已发运维群告警；Arvino 自身 4/4 Running+Ready、5 个端点均 200 未受影响，但**依赖 n8n 的工作流执行受影响**。升级是否收敛需下轮复核。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
2. **指定第一个真实任务**（哪个仓库、要达成什么）：外部 harness 接线后的端到端验收对象，也是团队任务的第一个载体。
3. **在 Qoder 设置页重贴十个角色的工作指令**（仓库文件已改，UI 不会自动同步；不重贴则团队成员仍不会被真正派发）。
4. **决定 11 个 blocked 看板任务**：补凭据、恢复执行，还是取消；并指定 firebird PR 审核由谁接。
5. **考虑提高 Qoder 当日用量上限，并确认巡检 job 夜间是否被触发**：账号日配额 10-21 16:05 用尽过（新会话一律 403 `Billing daily count exceeded`），巡检已改模型续命；而 10-21 22:30→10-22 11:02 巡检**整段未运行 751 分钟**（期间零轮次、零台账提交，根因未在本地判定），需查 job `bd0998dd` 的调度健康；明天同一时点配额可能再触顶。
6. **建议把平台仓库转为 private**：仓库现为 public，台账含内网主机名、ArgoCD 应用名、chat id 与本地路径（**无任何密钥**），建议转私有或把台账迁到私有库。

## 进行中

- **PR 队列（仅接管相关）**：接管分支 #29（台账与 contrib 全量入库）已合并；两个与接管相关的 open PR 按「有问题不合并」只记录原因——#28（总监守卫生效方式与现行单条红线治理冲突）、#23（harness 令牌变量改名已被主线超越），是否关闭或 rebase 由你定。
- **团队机制仍未跑通**：4 轮派发通道验证**均未通过**（协作 MCP 服务器未注册，工具面恒为 0）；backend / frontend / devops / tester 仍是零独立会话；本轮 5 条任务因通道不通**一条未派出**（未改派、未代做）。→ [注册核验](archive/registration-verification.json) · [派发验证](archive/dispatch-verification-2026-10-21.md)
- **T1 派发执行侧**：心跳侧已自动上线，但 `GET /work` 的派发队列仍无消费者（外部 harness 未部署）；方案（改哪里/风险/回滚）见设计稿。→ [设计](automation-dispatch-design.md)
- **候选分支并入 main**：租约代码所在分支（`candidate/t1-step-leases`）尚未并入 main，合并前需处理 alembic 链与镜像更新（线上仍跑 `e8fa6e6f3839`）。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **Qoder 团队启用**：团队已注册（1 leader + 4 member），等 UI 指令重贴后跑一次必须由成员动手的任务。→ [注册核验](archive/registration-verification.json)
- **拆派就绪、待通道**：6 条管理任务已建（派发通道实证 / 文件瘦身收尾 / PR#28+#23 处置材料 / harness 只读接线 / firebird-sea 任务书 / tester 异构模型），owner 与验收条件齐备，**全部 blockedBy「派发通道实证」**——第 4 轮复验仍为 0 工具，通道一通即可整队发出。

## 已完成

- 旧 Hermes 三个 cron job 全部暂停；10:42 起看板 0 running、0 新认领。
- **Temporal 降级已恢复（2026-10-22 05:57Z）**：`temporal-postgresql-0` 在 Pending/Unschedulable 115 分钟后调度成功到 zone b 新节点，temporal **7/7 Running 1/1**、history/matching 崩溃循环停止；容器日志实证崩溃的直接根因是 **DB 不可达**（`no usable database connection found`），此前「全集群 request 超卖为根因」的表述据此修正（超卖是并发条件、DB 不可达是直接因）。恢复是 GKE 节点池 web 升级**顺带**带来的，**节点组上限仍在**（`2 max node group size reached`）、自愈路径未打通，且 ArgoCD `temporal` 侧尚未转为 Healthy（下轮复核）。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- B3 接管模式上线：api/worker 运行 `e8fa6e6f3839`，`takeover_external_only=True`、`adapters=queue`。
- 活库迁移 `c3d4e5f6a7b8` 已执行：`executor_*` 5 列 + `repo_write_locks` 就位。
- **真实认领闭环已打通**：2026-10-21 14:51 live `run-ffb5465ea777` 的 `triage` 完成 claim→renew→complete（HTTP 200/200/200，token 未落盘，租约字段已清理）。→ [证据](archive/t1-step-leases-evidence.md)
- **Operator API 已恢复**：owner/agent 拆成独立 secret key（`owner-token`/`token`，值不同，SealedSecret 已同步重封），api 滚动重启中断 59s、0 重启；owner 实测 `POST /runs` 201、`cancel` 200，agent 建 run 仍 403、读接口 200。→ [证据](archive/t1-step-leases-evidence.md)
- **T1 账本心跳已上线生产集群**：CronJob `arvino-t1-heartbeat` 每 5 分钟自动做租约清单、派发可见性不变量与过期租约回收；本机 E2E（建 run→claim→过期→自动 reclaim→步骤回 pending）与集群验收均通过，生产 CronJob 已按 `*/5` 实际触发并成功完成。→ [证据](archive/t1-step-leases-evidence.md)
- **命名空间 NetworkPolicy 纳入 GitOps**：原先只存在于集群的 5 条策略已收编，带外漂移消除。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **GitOps 已收敛**：homelab main `a996720` 同步 Deployment 引用、SealedSecret 双键与心跳清单；`arvino-orchestrator-gke` Synced。
- Telegram 提醒通道已纠正并验证：根因是 chat id 配错（旧 id 已失效，非连接器故障）；24h 提醒改发 Arvin 私聊、巡检异常告警改发 k8sopenclaw 运维群，两处试发均成功。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- 巡检 job 已迁至新会话（熔断根因是账号日配额 + auto 分类器 400），17:30 起恢复 15 分钟节奏。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- T1 租约离线验收通过：claim/renew/release/reclaim、token 哈希、4h 总寿命、冲突审计、16 路并发压测。
- 只读巡检已接管并持续运行（每 15 分钟；15:00 轮捕获 ArgoCD 漂移、17:30 轮恢复迁移后节奏；**10-21 22:30→10-22 11:02 断档 751 分钟、期间零轮次，本轮已恢复**；24h 提醒基线已建，首轮真实提醒已于 10-22 11:34 发出，下次到点约 10-23 11:34）。→ [指导书](status-inspection-prompt.md)
- 单条治理红线已写入仓库 13 份角色文件；旧五条红线与 L0–L3 分级作废。
- **文件已瘦身并有入口**：13 个规划期产物移入 `archive/`，新增目录索引 README（必读只有 4 个：索引→速览→台账→心跳设计），Finder 重复件已删——**但 `docs/internal/.dup-backup/` 还剩 2 个漏网重复件**（`CHANGELOG_FRAMEWORK 2.md` / `3.md`），已拆派 devops 收尾。→ [索引](README.md)
- ArgoCD 全部 Application 均 Synced；例外均为**应用级状态位**（`arvino-orchestrator-gke` 陈旧 Degraded、`temporal`/`n8n-gke` Progressing、`image-updater` ComparisonError），资源级无非 Healthy。

## 未开始

- **外部 harness 接线（未开始）**：`scripts/agent-harness.sh` 现成（轮询 `/work` → 认领 → 跑 `claude -p`），本机 claude CLI 2.1.291 与 `CLAUDE_CODE_OAUTH_TOKEN` 就绪；缺的是接上租约协议的 claim→execute→renew→complete，且第一个真实任务待 Arvin 指定。续租目前由执行者自持 token 自续。
- **受控试点（真实业务写）**：firebird-sea 的首个业务写试点仍待 PR 审核归属确定后启动。
- **十角色异构模型与成员派发实证**：Arvino 标准流虽有 `qa`/`security` agent 步骤，但目前由同一外部队列执行；Qoder 团队 tester 需 UI 指令重贴后才能接入并读回实际模型家族——**当前还多一道前置：派发工具未挂载，tester 接不到任务**。

## 被阻塞

- **Qoder UI 指令**：需要你手工重贴，云端不会读仓库。
- **派发工具未挂载（2026-10-22 00:10，**根因已定案并收窄到单一能力位**）**：应用自带日志 `MCP session injection prepared` 给出决定性对照——团队任务会话为 `finalNames:[]`（**注入 0 个 MCP 服务器**，连 `builtin`/`node-repl` 都被清空），普通会话为 `builtinNames:['builtin','extension-market','node-repl','browser-use']` + 6 插件 = 10 个；三实例一致，团队任务三次全为空集。协作工具实为**应用进程内的 SDK MCP 服务器**（`lY0`/`_Se` 构造，`exposedName` 裸名暴露），**不**经本机路由器、**不**出现在 `--mcp-config`/`mcp_list`。**深挖后收窄**：Project/Discussion 可用性由**服务端下发的 5 个能力标志**决定（关键位 `discussionsEnabled`；本地兜底值 `u7` 全 false），本机启动日志为 **`Project Discussion access initialization completed {"enabled":false}`**（两实例各 1 次、均 false）；**本地无任何文件/环境变量入口**（不落盘、`QODER_AGENT_TEAMS` 本构建 0 命中）；每智能体「用于协作」= `collaborationEnabled` 本机 5 个 arvino 智能体**已全为 true**。已排除：成员未注册（5 名成员对当前讨论均有扫描序与有效 runtime 凭据）、本机 MCP 配置缺失、路由器故障、应用缺代码、版本可自愈。**探针未派发、5 条任务一条未派出、未改派、未代做、未绕行**。**官方文档已明确**：项目/讨论「**适用版本：企业版（Teams、Enterprise）。个人版暂不支持。**」→ 本机正是个人版（`personal_professional_trial` / `hasOrganization:false`）。**解除动作**：向 Qoder 支持确认付费个人版是否含项目/讨论，或为账号开通/加入**企业版**（重新登录、改本地配置均无效）；**询问材料已于 10-22 13:46 发出**（收件人 `support@qoder.com`，等回复；仓库内副本保留邮箱占位符）：[给 Qoder 的询问材料](archive/qoder-support-request-2026-10-22.md)；**验证口诀（三条同时成立）：启动日志出现 `{"enabled":true}`、注入日志 `finalNames` 非空集、`mcp_list` 非空**。→ [过程证据 §11–§14](archive/dispatch-verification-2026-10-21.md) · [开局简报](archive/team-session-brief-2026-10-21.md)
- **11 个看板任务**：调度已停，当前无执行者；最老已 129h，需逐项决定。→ [台账](../../docs/internal/SYSTEM_STATUS.md)
- **firebird PR 审核 / 夜间流程**：原 Hermes job 已停，暂无接管者。

## 已知但不阻塞

- **推 homelab main 前必须核对本地 HEAD 指向**：该仓有自动同步应用，推 main 会直接生效；今天已因此误带一条无关变更并回退。
- ArgoCD `arvino-orchestrator-gke` 应用级 `Degraded` 是 2026-10-19 的陈旧状态位（24/24 资源 Synced 且无非 Healthy）。
- `gitea.k8shome.com` 525、审批 webhook 404、agent-canvas 仅本机 8080：均为已知基线；Gitea 网页 525 时 open PR 核查可能不可读，内网 git 推送仍正常。
- 巡检每 15 分钟产生一次 `[skip ci]` 台账提交（约 96 次/日）；嫌吵可降为 30 分钟。
- canary `/house` 偶发单次 curl 000、重试即 200：判为瞬时抖动，继续观察。

---

**更新规则**：只写当前状态，不写历史；每项一行，技术细节一律外链；状态变化才更新（无变化不动），并同步首行时间；**只写接管项目范围内的事项**。逐轮历史见 [SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md)。
