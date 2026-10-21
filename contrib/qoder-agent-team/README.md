# Arvino 调度接管 · 文件索引

> 只关心「现在什么状态」→ 读 [STATUS_SUMMARY.md](STATUS_SUMMARY.md)（一页快照，给人看）。
> 只关心「什么时候发生了什么」→ 读 [../../docs/internal/SYSTEM_STATUS.md](../../docs/internal/SYSTEM_STATUS.md)（只增不删的审计台账，机器真相源）。
> 要改「巡检怎么跑」→ 读 [status-inspection-prompt.md](status-inspection-prompt.md)（巡检会话的权威作业指导书）。
> 要改「心跳/派发怎么做」→ 读 [automation-dispatch-design.md](automation-dispatch-design.md)（T0/T1/T2 设计 + 现状 + 下一步）。
> 其余是附录：角色指令（要贴进 Qoder 设置页才生效），或 `archive/` 里的历史产物。

## 活跃文件

| 文件 | 是什么 | 状态 |
|---|---|---|
| `STATUS_SUMMARY.md` | 人类一页快照：需要你动手 / 进行中 / 已完成 / 未开始 / 被阻塞 | 活跃，覆盖式更新；**只写接管项目范围内的事** |
| `status-inspection-prompt.md` | 15 分钟只读巡检的作业指导书（自动会话每次先读它） | 活跃，巡检的权威说明 |
| `automation-dispatch-design.md` | 自动调度层：T0 巡检 / T1 账本心跳 / T2 事件触发；§12 是已上线组件与下一步方案 | 活跃 |
| `TEAM.md` | 十角色团队的组成、职责边界、派发契约 | 活跃 |
| `roles.yaml` | 十个角色的结构化定义（模型/工具/禁止项） | 活跃 |
| `arvino-*.md`（13 个） | 每个角色一份工作指令正文 | 活跃，但**待贴进 Qoder 设置页**才对云端生效 |
| `archive/` | 规划期与验收期产物：接管设计稿/评审/预检/验收 JSON/注册手册/一次性 runbook/租约实现证据 | 历史，保留可追溯，**不再作为执行依据** |

## 相关工具（仓库 `scripts/`）

| 脚本 | 作用 | 状态 |
|---|---|---|
| `t1_heartbeat.py` | T1 账本心跳：租约清单、派发可见性不变量、过期租约回收 | 活跃，已随 GitOps 以 CronJob `arvino-t1-heartbeat`（每 5 分钟）跑在生产集群 |
| `agent-harness.sh` | 外部执行器：轮询 `/work`、认领步骤、跑 `claude -p` | 待接租约协议（下一段工作） |
| `gitea-open-prs.sh` | 只读 Gitea open PR 摘要（巡检用，凭据运行时读 keychain） | 活跃 |
| `director.py`、`director-heartbeat.py` | Hermes 时代的 director 与心跳（监控委派记录、给 Hermes runtime 喂 dispatch-queue） | **遗留**：Hermes 三个 job 已暂停，不在接管链路内；保留仅供追溯 |

## 约定

- 速览只写**接管项目范围内**的事项；与接管无关的业务应用变更只记台账，不进速览。
- 台账 `docs/internal/SYSTEM_STATUS.md` 只增不删；速览是覆盖式快照，无实质变化不动。
- 链接到已归档文件时用 `archive/<文件>` 路径。
- 唯一红线：不泄密 / 不输出密钥。
