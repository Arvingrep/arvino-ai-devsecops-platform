# Arvino 系统状态台账

> 由 cron job `arvino看板总监值守` (0c82db48c322) 每 15 分钟更新。
> 这是总监掌握全局的唯一真相源——不是聊天记录,是持久化文件,Arvin 随时可翻。
> 每条记录:组件 | 真实健康检查方法 | 最后确认时间 | 状态 | 负责人

## 调度层(唯一)

| 组件 | 健康检查 | 最后确认 | 状态 | 负责人 |
|---|---|---|---|---|
| Hermes kanban (arvino board) | `hermes kanban list --json` + `dispatch` | 2026-10-16 20:04 | 🟡 48 done / 4 running / 0 blocked | 总监(cron 0c82db48c322) |
| arvino API/Worker/OPA/Postgres (GKE) | `curl healthz` + authenticated `GET /projects` + pod status | 2026-10-16 20:04 | 🟡 healthz 200、4 pods Running 0 restarts；现有巡检token访问`/projects`为401，t_6d0dfca1修复中 | arvino-devops |
| Temporal (GKE) | pods + `temporal operator namespace describe default` | 2026-10-16 20:04 | ✅ 7 pods Running；namespace `default` Registered/Normal | arvino-devops |
| n8n webhook relay | `curl /healthz` | 2026-10-16 20:04 | ✅ 200 `{"status":"ok"}` | arvino-devops |

## 已废弃,不再维护

| 组件 | 原因 | 日期 |
|---|---|---|
| ai-company-oss 10-Bot 路由(本地Docker) | 与kanban调度重复,部署在MacBook本地无法24h存活,DNS故障崩溃35h无告警 | 2026-10-16 |

## 待修/待裁决

| 组件 | 问题 | 负责人 | 期限 |
|---|---|---|---|
| ArgoCD (mac-mini-orbstack隧道) | 20:04复验`127.0.0.1:26444`仍connection refused；t_7fdf1373修复中 | arvino-devops | 修复或升级给Arvin |
| agent-canvas (OpenHands) | 20:04根路径200/29104 bytes；仍待观察是否有真实任务 | 总监观察 | 3周无真实使用则下线 |
| arvino实时dashboard | 20:04独立复验根路径、`/dashboard`、`/ui`均404（`/docs`为200）；此前t_a6c8a486“done”不成立，t_7bb13686修复中 | arvino-frontend | 必须以live API页面真实200验收 |
| firebird-sea `/shop` | PR#117已合并且QA成功，但总监侧域名DNS无法解析，尚未完成用户路径复验；t_5d19f7a9从canary独立验收中 | arvino-tester | 得到真实HTTP/功能证据后再判已修 |

## 告警通道(已验证)

- Telegram 私聊 `@wcd_admin_bot` → chat_id `2057618975`,2026-10-16 已用curl直测拿到 `message_id: 1990` 确认送达
- 群 `-5351926143` 的订阅**已全部作废**(指向无凭证的profile,从未真正送达),不再使用

## firebird-sea 待处理(owner审批卡着的)

| PR | 内容 | 状态 |
|---|---|---|
| #115 | ci-verify.yml 补QA门禁覆盖arvino/*分支 | Open,待Arvin审 |
| #116 | canary模块路由404根因修复(6/7路径已验证200) | Open,待Arvin审 |
| #117 | /shop 500 PHP根因修复 | 2026-10-16 19:46 已合并；运行时独立复验中(t_5d19f7a9)，未先判已修 |

## 真实未认领业务backlog: 25个Issue,0个被自动认领(2026-10-16查)。

## 巡检历史（追加，不覆盖）

- 2026-10-16 20:04 PST：dispatch无遗留blocked；发现#117已合并（状态变化），但外部DNS无法解析，已派独立canary验收。发现t_a6c8a486虽为done，实时dashboard三个候选路径仍全部404，纠正为未完成并派修复。ArgoCD隧道仍拒绝连接。API healthz正常但现有巡检token访问`/projects`为401。新建并启动4个任务：t_7fdf1373、t_7bb13686、t_6d0dfca1、t_5d19f7a9；全部订阅唯一有效DM `2057618975`。

## 巡检更新 2026-10-16 20:45
- ArgoCD 隧道仍不可达 (connection refused)。
- arvino 实时dashboard `/dashboard` 与 `/ui` 仍返回 404，未恢复。
- arvino API `/projects` 访问仍返回 401，当前 token 失效。
- OpenHands agent-canvas 页面返回 200，未出现 503，已恢复。
- n8n webhook、Temporal、所有 GKE pods 状态正常。
- 任务 t_7fdf1373、t_7bb13686、t_5d19f7a9、t_6d0dfca1 已完成并在 DM 2057618975 订阅。

---

(记录完毕)