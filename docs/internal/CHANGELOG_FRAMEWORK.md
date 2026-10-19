# Arvino 框架变更日志

> **铁律(2026-10-16 Arvin 确立)**：任何对框架/角色/流程的新增或修改,必须在本文件追加一条记录,
> 否则该变更视为**不可行、不生效**。新加入的任何 LLM/Agent 都必须先读这份文件再动手,
> 不允许"先做事后补文档"或者不补文档就上线。每条记录 Arvin 必须能一眼审完。
>
> 格式: `日期 | 变更类型 | 具体内容 | 谁做的 | Arvin是否已审`

## 待 Arvin 审核的变更(本次会话产生)

| 日期 | 类型 | 内容 | 操作者 | Arvin审核 |
|---|---|---|---|---|
| 2026-10-16 | 废弃 | ai-company-oss 10-Bot 本地Docker路由系统:容器/卷/镜像/目录已彻底删除(非归档) | 总监会话 | ⬜ 待审 |
| 2026-10-16 | 清理 | 孤儿容器 arvino-pg 删除(不在docker compose管理范围,10-14遗留) | 总监会话 | ⬜ 待审 |
| 2026-10-16 | 新增 | docs/internal/SYSTEM_STATUS.md 系统状态台账,cron job 0c82db48c322 每15分钟读写 | 总监会话 | ⬜ 待审 |
| 2026-10-16 | 修复 | GitHub webhook(firebird-sea)补充订阅 issues/issue_comment 事件(此前只订阅check_run/pull_request/status,新Issue完全无法触发自动化) | 总监会话 | ⬜ 待审 |
| 2026-10-16 | 新增 | kanban任务 t_2c1a2492: 需求闭环(Issue创建->n8n->arvino自动建Run) | 总监会话,assignee arvino-architect | ⬜ 待审,进行中 |
| 2026-10-16 | 修复 | 告警通道从群-5351926143(从未真正送达)改为telegram DM 2057618975(@wcd_admin_bot,已用curl验证message_id真实送达) | 总监会话 | ⬜ 待审 |
| 2026-10-16 | 待办 | @wcd_admin_bot 尚未加入Arvino群,群内角色动态播报功能未激活,需Arvin手动拉群 | — | ⬜ 待办 |
| 2026-10-16 | 修复 | PR#17(CI lint修复)、PR#19(CI全部lint修复)已合并;PR#115/#116/#117(firebird-sea QA门禁+canary路由修复+/shop 500修复)待Arvin审 | 总监会话 | ⬜ 待审 |
| 2026-10-20 | 调研 | 6个custom(阿里云百炼/qwen3.8-flash,按token计费)角色切订阅方案调研结论:openai-codex当前429封墙(5.5天后恢复),唯一可用订阅为anthropic-oauth-1;建议:openai-codex恢复后分流(部分切anthropic,部分留codex),当前不改配置、维持现状避免触发anthropic新429 | arvino-architect | ⬜ 待Arvin/orchestrator审核后方可执行 |
| 2026-10-20 | 执行 | provider分配方案按新规则("非红线决策不用问")直接拍板执行:devops+tester切anthropic-oauth-1;backend/frontend/researcher/design维持custom(等openai-codex 429解除后再切,当前429剩5.5天,不急);architect/orchestrator/security/smartcontract维持openai-codex。理由:避免10角色全挤一个池再触429;devops是最高频角色单独走anthropic比混进codex池更稳;不花钱(anthropic走订阅非按量)不碰生产不涉合并,属非红线运营决策 | arvino-orchestrator | 已验证✅ (arvino-devops task t_1f8ffb8b: 两个profile config.yaml已从provider:custom/qwen3.8-flash切为provider:anthropic/claude-sonnet-4-20250514,auth走anthropic-oauth-1 priority=0;hermes chat -q echo测试两个profile各独立跑通,exit 0,session各2s) |

| 2026-10-20 | 规则变更 | **决策权收窄生效**:非红线类决策(技术方案选择/代码合并非生产/测试环境操作/provider-LLM路由等运营细节)一律"不用问Arvin,总监直接拍板执行+事后在本文件/Telegram报告",不再逐条clarify或等Telegram回复。红线不变且不受此条影响:不花钱、不碰生产集群写操作、不输出密钥、不绕过QA门禁、不自己合并PR——这5条任何时候都必须Arvin逐条单独批准。本条由Arvin在对话中直接授权("把需要我授权的规则都去掉,按实现目标为最高级别...",总监当场说明红线不可豁免并澄清范围,Arvin确认"只把非红线类决策...全部改成不用问,直接做+事后报告,红线不动") | arvino-orchestrator | ✅ Arvin已在对话中确认 |

## 变更规则(后续所有LLM/Agent必须遵守,写入skill同步生效)

1. **先写变更记录,再动手，或至少动手后、报告给Arvin前必须补上** — 没有记录的变更一律视为"未发生"，下次巡检发现没记录的改动要撤销或补记录后才算数。
2. **每条记录必须是 Arvin 能在10秒内看懂的一句话**，不是技术人员才看得懂的黑话。
3. **凡涉及新增/修改 10 个数字员工的角色定义(SOUL.md/SKILL.md/规则)**，必须单独起一条记录，哪怕只改一行职责描述。
4. **Arvin审核列未打勾之前，不得视为"框架已确认"**，上游任务(比如新项目复用这套基建)不能以未审核的变更为前提开工。
5. 本文件只增不删历史记录(即使后面废弃也是追加一条"废弃"记录,不是删掉旧记录)——保证可追溯。

| 2026-10-20 | 事故+执行 | **百炼 custom 池全量失效(HTTP 403 Access to model denied)**:qwen3.8-flash 在 workspace ws-cree7h08… 上对所有角色返回"not eligible for using the model",导致 devops/backend/tester 的 4 张卡 worker 初始化 20s 内 exit 78 全部 blocked(诊断只报 repeated_failures/终端 provider 错误)。实测 5 个 profile 默认配置全 403,而 anthropic OAuth(#1 hermes_pkce)探针 OK。**已直接处置(非红线:provider 路由属运营决策,不花钱/不碰生产/不合并)**:devops / tester / backend / architect / security 五角色 config.yaml 的 model 段统一改为 `claude-sonnet-4-6` + `provider: anthropic`,原文件备份为 `config.yaml.bak-<时间戳>`;修后 4 张卡清零 counter+unblock+dispatch 全部真实 running。design/frontend/researcher 暂无待办,沿用 custom(同样 403,待有活时再切或等百炼恢复)。architect/security 原 provider openai-codex 已 429 封墙 5d12h,一并切 anthropic 避免空转 | 总监(cron 0c82db48c322) | ⬜ 待审 |

|| 2026-10-20 | 纠正 | 上一条的模型名写错引发二次故障:t_1f8ffb8b 的 worker 执行"切 provider"时把 devops/tester 的 `model.default` 写成 `claude-sonnet-4-20250514`,该模型在本 OAuth 账号下 `HTTP 404: model` 不存在 → 3 分钟内 4 张卡(含 QA)二次崩。总监复验日志后改为 `claude-sonnet-4-6`(与 architect/security 一致、探针实测 OK),并把被 worker 顺手改回 `custom` 的 backend 一并改回 anthropic。**规则教训:让 worker 改 LLM 配置属于高风险动作,目标文件应把"正确模型名"作为验收断言写明,否则 worker 会按自己的偏好填一个不存在的模型** | 总监(cron 0c82db48c322) | ⬜ 待审 |
|| 2026-10-20 | 新增 | **Arvin审批收件箱机制上线(n8n版)**:1)新建 `/Users/arvin/.hermes/scripts/approval-actions/` 预注册脚本目录(alembic_stamp_head.sh/merge_pr.sh等危险操作);2)新建 `kanban_notify_backstop_n8n.sh` 1分钟轮询检测blocked(needs_input)任务→调用n8n webhook `https://n8n.k8shome.com/webhook/arvino-decision-request`(不再直接curl Telegram文字);3)新cron job `6d0ade690bc8` 每1分钟执行backstop脚本,deliver=local;4)按钮回调通过callback_data编码 `v1|{task_id}|{action_type}|{action_payload}`,n8n的Execute Command节点处理callback_query并映射执行hermes kanban命令或预注册脚本,不经过LLM推理;5)幂等性通过本地state文件实现(task_id:status去重)。**n8n workflow配置待完成**:需在n8n.k8shome.com导入/配置webhook+Telegram节点+callback处理逻辑 | arvino-devops(task t_a4886920) | ⬜ 待审+n8n配置待实施 |
| 2026-10-20 | 新增 | **总监不得下场执行守卫机制落地(Director rule 1，2026-10-20 决策权收窄条款)**：`scripts/director_guard.sh` 可执行守卫脚本，`source` 后暴露 `director_check <cmd>`；同一事件内第1条非只读命令打 WARNING(exit 0)，第2条触发 HARD BLOCK(exit 2)。只读白名单：`git log/diff/show/status`、`kubectl get/describe/logs`、`gh pr view / issue view / run view / gh api -X GET`、`curl GET`、`cat/ls/find/grep/jq/yq/helm template/lint`。`scripts/test_director_guard.sh` 自测脚本（7断言全 PASS）输出：`✅ PASS: readonly git log`→`✅ PASS: non-readonly #1 WARNING exit 0`→`✅ PASS: count=1`→`✅ PASS: readonly kubectl logs`→`✅ PASS: count still 1`→`🚫 HARD BLOCK exit 2`→`✅ PASS: log ≥2 entries`→`ALL PASS`。技术依据：Director rule 1（2026-10-20 决策权收窄：非红线运营决策由总监直接执行+事后报告）+ `.agents/RULES.md §6.3` + `AGENT_PIPELINE.md GitOps铁律`。PR: fix/director-guard-mechanism → main | arvino-architect(task t_1aa7cbdb) | ⬜ 待审 |
| 2026-10-20 | QA返修 | **白名单 flag 前置漏匹配已修(task t_27fa316a)**：`kubectl\\ get\\ *` 改为 `kubectl\\ *get\\ *`，支持 `--context/--kubeconfig/-n` 等 flag 出现在子命令之前。新增5条断言：`kubectl --context gcp-gke -n arvino get pods`→只读✅；`kubectl --context mac-mini-orbstack -n argocd get applications`→只读✅；`kubectl -n arvino logs deploy/arvino-api --tail=50`→只读✅；`kubectl --context gcp-gke -n arvino apply -f x.yaml`→非只读 WARNING✅；`kubectl --context gcp-gke -n arvino exec -it pod -- sh`→非只读 HARD BLOCK✅。自测输出：`ALL PASS`，exit 0（12断言全通过）。 | arvino-architect(task t_27fa316a) | ⬜ 待审 |
