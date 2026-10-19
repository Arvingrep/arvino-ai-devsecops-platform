# Arvino AI DevSecOps Platform — 项目章程

> 本文档供外部 AI/Agent 审核讨论使用。写于 2026-10-16,所有现状描述均为当日实测结果,非设想。
> 2026-10-16 已收到5方AI审批(见第7节),第7节为待Arvin确认的修订提案,确认前不改变上文既有规则。

## 1. 项目目的

给一人公司(Arvin 独立运营)搭建一套**可复用的 AI 研发团队基建**——不是做一个产品,是做一套"骨架":
需求进来 → 10 个专业角色按标准流程(需求/架构/开发/测试/部署)接力完成 → 独立验证通过 → 交付,
全程闭环,过程里 Arvin 只做真正该人决策的部分(审批、花钱、架构方向),其余自动跑。

**明确排除的目标**:
- 不是"又一个 AI 公司套壳产品"(README 原话:"Not another AI company wrapper")
- 不是为了用工具而用工具——只做"没有现成工具能做"的那部分(工作流编排 + 跨工具台账 + 角色间的不变量约束),
  现成的工具(Plane管看板、Temporal管可靠调度、Argo CD管部署、OPA管策略、Trivy管安全扫描)一律直接用,不重造
- 基础项目做实之前,不接入第二个业务项目——firebird-sea 是唯一的验证对象(项目一),
  验证标准是"一句话/一个Issue进去,系统自动走完全流程并闭环",做到之前不复制给项目二/三/四

## 2. 核心需求(闭环的四个环节)

```
需求输入 ──> 角色接力执行 ──> 独立验证 ──> 闭环收口
```

### 2.1 需求输入(可持续、规范化)

两条通道,都落到同一条自动化路径,不允许各自为政:
- **通道A**:Arvin 在 GitHub 开 Issue
- **通道B**:Arvin 跟 Telegram 里的 orchestrator 角色说一句话,由它转建 Issue

新 Issue 产生后,必须能自动触发工作流(目前刚修复 GitHub webhook 漏订阅"issues"事件这个根因性缺口,
自动建Run的落地代码仍在验证中,见第5节现状)。

### 2.2 十个角色,按不同流程规则接力执行

| 角色 | 职责 | 模型/成本分层 |
|---|---|---|
| orchestrator | 拆解需求、派发任务 | Claude(推理类) |
| architect | 技术选型、C4架构图 | Claude(推理类) |
| backend | API/数据库/鉴权 | 百炼DeepSeek(编码类) |
| frontend | React/Next.js UI | 百炼DeepSeek(编码类) |
| devops | Docker/CI/CD/部署 | 百炼DeepSeek(编码类) |
| tester | 单测/e2e/覆盖率,**必须独立于开发者** | Claude(评审类,与开发用不同模型家族,避免共同盲区) |
| security | OWASP审计、最终关卡 | Claude(评审类) |
| researcher | 调研、服务选型 | 百炼Qwen(多样性) |
| design | UI/UX规范、设计token | 百炼Qwen(多样性) |
| smartcontract | 智能合约,**强制人工审批无例外** | Claude |

每个角色有独立的规则文件(职责/能力/编码规范/输出格式),外加一份全员共享的公司红线
(安全伦理、审批门禁、代码质量、失败3次升级给Arvin)。

**三档工作流**(按风险分级,不是一刀切):

| Profile | 场景 | 流程 | 人工审批次数 |
|---|---|---|---|
| Fast | Bug/配置微调 | triage→develop→test→PR→CI→gate→preview | 0(门禁自动挡受保护路径) |
| Standard | 功能开发 | spec→架构→前后端→CI→QA+安全→评审→gate→preview→**审批**→生产 | 1 |
| Governed | 架构/基建/迁移 | 调研→spec→ADR→**审批**→实现→CI→QA+安全+IaC→评审→gate→staging→**审批**→生产→验证 | 2 |

### 2.3 独立验证(不是自报)

**强制完成契约**:任何任务标记"完成"之前,必须有独立于执行者的验证动作(curl端点/读文件/重跑查询),
不允许只凭执行者自己的summary就算数。这条是2026-10-16定的铁律,起因是连续两次被自报"完成"但实测是假的
(canary配置修复自报完成但实测404;CI"修复"PR合并后仍有53条历史lint债务未清)。

评审角色(tester/security)必须用**跟开发不同的模型家族**,避免同一个模型自己审自己的盲区。

### 2.4 闭环收口

闭环=独立验证通过 + (涉及生产时)Arvin本人审批。
Arvin 收到的应该是"验证通过"的确定性消息,不是"Agent说完成"的转述。

## 3. 治理规则(优先级从高到低)

1. **最高治理原则**(2026-10-16 Arvin授权):Arvin 本人的指令如果没有技术/工具支持,
   Agent 有权直接判定"不可执行"并说明技术原因;项目目标高于字面指令。
   **不覆盖红线**,红线依然必须先问。
2. **变更文档铁律**:任何框架/角色/流程变更必须写入 `docs/internal/CHANGELOG_FRAMEWORK.md`,
   且必须附"技术依据"(实测证据 / 设计原则引用 / Arvin原话指令三选一,不能是"我觉得"),
   否则该变更视为不生效。Arvin 审核栏未打勾之前不算"框架已确认"。
3. **红线**(此条不受第1条豁免):不花钱、不动架构决策、不碰生产集群写操作、不输出密钥、
   不绕过QA门禁、不自己合并PR。Terraform apply/destroy 允许Agent执行但必须先经clarify工具拿到Arvin本人确认。
4. **代码层强制 > 文字约定**:能用代码约束的规则(比如 GitProvider 根本没有 merge() 方法)
   优先于只写在提示词里的规则,后者历史上证明容易失效。
5. **Director 只派发不执行**:技术总监角色不亲自写代码/改配置,通过kanban建任务派给对应角色,
   自己亲自动手过一次(已记录并改正)。

## 4. 技术栈与组件(调度层只能有一个)

- **调度层(唯一)**:Hermes kanban(arvino board)+ `arvino-devsecops-platform` 引擎
  (Python/FastAPI/Pydantic v2/SQLAlchemy 2.0/OPA-Rego/Temporal)
- **执行后端**:ClaudeCodeExecutor(当前实际在用)+ OpenHandsExecutor(代码/测试已就绪但激活开关未打开,
  设计目的是Claude Code不可用时的备用执行路径)
- **Webhook中继**:n8n,统一转发 Plane/Gitea/Nightingale 事件到 arvino API
- **看板UI**:Plane(面向人看的项目管理界面,不是调度引擎)
- **GitOps部署**:Argo CD(状态待确认,见第5节)
- **已废弃**:ai-company-oss(10个独立Telegram Bot的本地Docker路由系统)——
  2026-10-16已彻底删除(容器/镜像/卷/代码目录),原因:部署在MacBook本地无法24h存活,
  DNS故障崩溃35小时无告警,且与kanban调度功能重复

## 5. 当前真实状态(2026-10-16,实测,不是计划)

✅ 已验证健康:arvino API/Worker/OPA/Postgres(GKE)、Temporal(曾不稳定已自愈)、n8n、Hermes kanban调度
⚠️ 已部署但未实际使用:agent-canvas/OpenHands(只有健康检查心跳,0次真实任务,激活开关未设置)
⚠️ 待确认:Argo CD(查询隧道中断,未能确认GitOps同步状态)
❌ 需求自动认领闭环:GitHub webhook 刚修复事件订阅缺口(之前只订阅PR/CI事件,新Issue完全无法触发),
   "新Issue自动建Run"的代码逻辑仍在验证中(kanban任务 t_2c1a2492)
❌ firebird-sea 业务backlog:25个Issue,实测0个被自动认领,是本项目"做实"与否的核心验收指标
⏳ 待Arvin审批:PR #115(QA门禁覆盖修复)、#116(canary路由404修复)、#117(/shop 500修复)

## 6. 给外部AI/Agent审核的开放问题

1. "调度层只能有一个"这个决策是否合理?有没有更适合多Agent编排的现成方案?
2. 10角色的模型分层(推理类用Claude/编码类用DeepSeek/多样性用Qwen)是否是合理的成本/质量权衡?
3. "评审角色必须用不同模型家族"这条设计假设(避免同模型自己审自己的盲区)有没有实证支持或反例?
4. 需求自动认领闭环(webhook→自动建Run→工作流自动推进)还有哪些环节容易隐性失效,
   本项目目前还没遇到但该提前防范?
5. "完成契约必须独立验证"这条规则,在多Agent协作场景下有没有更成熟的工程实践可以参考?

> **可信度警告(2026-10-16 22:40)**：本文件第7节"审批意见与修订提案"内容来源不明——总监会话排查了cron运行日志、kanban任务记录、git历史三条线索均无对应证据，无法确认"四方AI审阅"是否真实发生。**在溯源清楚或重新真实执行审核之前，第7节内容不采信，不作为决策依据。**

## 7. 审批意见与修订提案(2026-10-16,待 Arvin 确认)

**依据**:Codex、Antigravity、Claude Code、Hermes 四方独立审阅本章程,结论均为「有条件批准」;WorkBuddy 两次启动均报 `ACP connection closed`,未取得意见。
以下只收录**至少两方一致**、且能对应到本章程具体行号的问题。按第3.2条,这些是提案,Arvin 在 CHANGELOG_FRAMEWORK 审核栏打勾前**不生效**;其中涉及架构与红线的取舍属 Arvin 决策,Agent 不得自行选定。

| # | 问题(章程位置) | 依据 | 修订提案 | 谁决定 |
|---|---|---|---|---|
| 1 | Fast 档"0次人工审批"(L55)与红线"不自己合并PR"(L81)冲突 | Codex/Antigravity/Claude/Hermes 一致;GitProvider 无 merge()(L82) | 写明合并方式:Agent 永不合并;Fast 档由 GitHub Branch Protection + Auto-merge 在 CI、OPA gate、QA/Security 检查全部 SUCCESS 后合入。同时修复 PR#115 使 QA 门禁为 SUCCESS 而非 SKIPPED | Arvin |
| 2 | "调度层唯一"但 Hermes kanban、arvino 引擎、Temporal 并列(L89-90) | 四方一致认为有状态分叉风险 | 指定唯一权威状态源(Antigravity 建议 Temporal;kanban/Plane 仅作视图投影),并写明任务归属、重试、超时、补偿由谁负责 | Arvin(属架构决策) |
| 3 | 独立验证定义不足(L61-65) | 四方一致:异构模型只降低共同盲区,不证明独立 | 定义为"不同执行主体 + 独立证据源 + 预先定义的验收标准";验证回执至少含 command、exit_code、输出摘要、环境、验证者角色、被验证版本;证据存放位置与"置完成"权限须明确;模型异构仅作辅助 | Arvin |
| 4 | 缺少量化验收与止损(L15-16、L107) | 四方一致 | 增加指标:自动认领率、端到端成功率、错误完成率、人工介入率、P95周期、失败发现时延;设里程碑(先1个Issue端到端,再扩大)与止损条件。具体数值待 Arvin 填 | Arvin |
| 5 | 自动认领入口缺保护(L30-31、L105-106) | 四方一致;firebird-sea 现有25个存量Issue,放开瞬间会同时涌入 | 增加:Webhook 签名校验与防重放、幂等键(repo+issue_id)、同一Issue仅一个活跃Run、全局最大并发数、机器人自产事件过滤、Issue内容按不可信输入处理(防提示注入)、定期对账Issue数与Run数 | Arvin |
| 6 | 治理第1条"项目目标高于字面指令"授权过宽(L74-75) | Codex、Claude 指出 | 补充:判定"不可执行"须记录依据并通知 Arvin;只可报告不可执行并提替代方案,不得借此扩大授权或越过红线;谁判定红线须明确 | Arvin |
| 7 | 审批链缺口(L53-57、L69) | Claude(Arvin单点,PR#115-117待审)、Hermes(公司红线要求生产部署 DevOps+Security 双签;smartcontract 须逐动作审批) | 增加审批SLA、超时升级、Arvin不可用时的处理;生产审批绑定具体提交/镜像/配置,内容变化须重批;Governed 档与 smartcontract、双签规则对齐 | Arvin |
| 8 | 缺故障恢复、预算、安全与可观测性(全文无) | 四方一致 | 新增:Agent卡死/重试耗尽/CI超时/部署后验证失败的补偿与回滚责任;月度成本上限与告警(与"不花钱"红线对齐);凭证最小权限与生命周期;Agent行为日志与失败率指标;"失败3次升级"的计数范围与重置规则 | Arvin |
| 9 | 现状缺证据(L100-108) | Codex、Claude、Hermes | 每条"健康/已自愈"附时间、版本、探测范围、证据链接与观察窗口;Argo CD 状态必须确认;OpenHands 限期启用或下线 | Arvin / devops |

### 进入正式批准前的共同条件(四方交集)
1. 上述1-9项经 Arvin 确认并记入 CHANGELOG_FRAMEWORK(附技术依据)。
2. 在 firebird-sea 用真实 Issue 至少跑通一次完整链路:Issue → 自动建Run → 角色接力 → CI → 独立QA/Security → PR → 审批 → 预览/部署 → 独立验证 → 收口,并覆盖重复事件、执行中断、QA拒绝、审批拒绝与超时。
3. 核心闭环达到约定指标前,不接入第二个项目,不宣称生产就绪。

### 审批记录
| 审批方 | 结论 | 备注 |
|---|---|---|
| Codex | 有条件批准 | 不构成生产操作、付费或合并PR的授权 |
| Antigravity | 有条件批准 | 要求 Fast+Standard 各一个真实Issue做金丝雀验收 |
| Claude Code | 有条件批准 | 要求处置 OpenHands、设成本上限 |
| Hermes | 有条件批准 | 方向批准,运行能力暂不验收 |
| WorkBuddy | 未取得 | ACP 连接失败,待修复后重发 |
| Arvin | ⬜ 待审 | |
