# 十角色注册操作卡（GUI 手工执行版）

## 0. 这份卡是什么

Arvin 2026-10-21 决定并行推进两条线，本卡是其中"UI 层注册"这条线的**可执行脚本**：你在 Qoder 设置页照着点，约 30–60 分钟即可让智能体团队跑起来。

- 本卡**不是**已完成注册的证明。执行前 `registration_status` 仍是 `prepared_not_registered`；执行后须另写一份 `registration-verification.json` 证据，才可改为已注册。
- 本卡**不构成**任何业务写授权。注册成功只代表团队可路由、可派只读任务；`takeover-approval-request.md` 未获批前，业务写任务保持阻塞。
- 依据：`registration-verification-checklist.md`（核验清单）、`TEAM.md`（职责与流程）、`roles.yaml`（角色索引）、Qoder 官方文档《自定义智能体》《自定义智能体团队》《模型选择器》。

### 为什么必须手工点

Qoder 官方文档给出的创建路径**只有 GUI**（自定义智能体是"从空白创建 / 从模板创建"，团队是在设置页新建），**未提供本地角色文件批量导入或注册 API**（已读文档未提及；"未提及"不等于能力必然不存在，但本轮没有任何可用的程序化注册通道）。本会话的 computer-use 通道未连接（`mcp_list` 查询该服务不存在），此前两轮尝试被工具拒绝。因此剩下的是纯手工 GUI 操作，本卡把其中的判断全部预先做完，你只负责复制粘贴。

### 你截图里那个页面

你贴的图是 **设置 > 智能体团队** 页。当前只有两个内置模板（专业写作、研发交付组）且开关关闭，那是 Qoder 的示例，**与 Arvino 无关，不要动**。本卡要建的是一个新的 Arvino 团队。

---

## 1. 前置条件（开工前逐条打勾）

| # | 前置 | 判据 | 状态 |
|---|---|---|---|
| P1 | 设备在线、Qoder 已登录 | 设置页可正常打开并可保存 | ⬜ |
| P2 | 未开 Full Access | 设置里权限模式为 Auto Approval 或 Ask | ⬜ |
| P3 | 未改登录/订阅/凭据 | 开工前后各读一次设置并留证 | ⬜ |
| P4 | 未产生付费模型调用 | 不跑业务模型探针；模型资格用界面显示确认 | ⬜ |
| P5 | 已读 `TEAM.md` 与 `roles.yaml` | 知道十个职责和边界 | ⬜ |

**P1 特别说明**：官方文档明确"团队任务通过当前设备执行，使用时需要保持设备在线"。设备离线时团队任务会失败。所以不要把它开在你要合上电脑的时段。

---

## 2. 第一步：读回模型可见清单（不要跳过）

在 **设置 > 模型** 打开模型选择器，把**你当前订阅里实际可见**的模型逐条抄下来（含档位 Auto / Fast / Efficient 与具体模型名）。**不要凭记忆填模型名**——这一版有一整轮事故就是模型名填错导致 4 张卡连续崩溃。

记录格式：

```
模型可见清单（抄录自 设置 > 模型，抄录时间：____）
档位：Auto / Fast / Efficient / Ultimate / 其他：__________
具体模型（逐个抄名称）：
  1. ______   倍率 ____
  2. ______   倍率 ____
  ...
```

抄完后**同时记下厂商/家族归属**（例如某模型属于 A 厂 / 某模型属于 B 厂）。异构模型判定靠的就是这个，不是靠角色名或会话 ID。

> 注意 Credit 倍率：Auto ≈0.5×、Fast ≈0.8×、Efficient ≈0.3×（活动期可能 0×）、Ultimate ≈2.0×，具体模型另有倍率（文档示例：某 Flash 系 0.1×、某 K 系 1.4×，仅为示例不是本账号实际值）。子 Agent 调用特定模型**另计费用**。选型时优先用订阅内、倍率低的。

---

## 3. 第二步：创建五个智能体（首批席位）

首批席位按 Arvin 2026-10-21 决定：**lead + backend + frontend + devops + tester**（security 走独立会话补，见 §7）。

### 3.1 通用规则

- 入口：**设置 > 智能体 > 创建智能体 > 从空白创建**。
- **名称创建后暂不支持修改**，十四个字符级别的名字必须一次敲对。名称一律用 `roles.yaml` 里的 `name` 字段，不要翻译、不要加后缀。
- **工作指令不要重写**：正文已经写好了，照抄即可。抄法（终端执行，输出即工作指令全文）：

  ```bash
  cd /Users/arvin/Documents/arvino/ai-devsecops-platform/contrib/qoder-agent-team
  awk 'BEGIN{c=0} /^---$/{c++; next} c>=2{print}' arvino-lead.md
  ```

  把 `arvino-lead.md` 换成对应角色文件名。

- **说明**字段：抄 `roles.yaml` / 角色文件 frontmatter 里的 `description` 原文（含"待注册、待试点验收"这句也照抄——等试点验收完成再回来改，避免两处说法不一致）。
- **默认模型**：见 §3.3 选型规则。
- **工具权限**：见 §3.4 三桶映射。**允许列表 = 免审批执行；禁止列表 = 阻止；其余 = 遵循默认权限（走审批）**。

### 3.2 五个智能体的表单

| 字段 | arvino-lead | arvino-backend | arvino-frontend | arvino-devops | arvino-tester |
|---|---|---|---|---|---|
| 名称 | `arvino-lead` | `arvino-backend` | `arvino-frontend` | `arvino-devops` | `arvino-tester` |
| 说明 | 抄 frontmatter | 抄 frontmatter | 抄 frontmatter | 抄 frontmatter | 抄 frontmatter |
| 工作指令 | `arvino-lead.md` 正文 | `arvino-backend.md` 正文 | `arvino-frontend.md` 正文 | `arvino-devops.md` 正文 | `arvino-tester.md` 正文 |
| 允许（免审批） | Read, Grep, Glob | Read, Grep, Glob | Read, Grep, Glob | Read, Grep, Glob | Read, Grep, Glob |
| 禁止 | — | — | — | — | **Edit, Write, NotebookEdit** |
| 其余（走审批） | Edit, Write, Bash | Edit, Write, Bash | Edit, Write, Bash | Edit, Write, Bash | Bash |
| 默认模型 | 实现者族（见下） | 实现者族 | 实现者族 | 实现者族 | **必须异构族** |

> **tester 的禁止列表是硬要求**：它从工具层面保证独立测试员改不了被测实现。这条不能省。

### 3.3 默认模型选型规则

官方文档确认每个自定义智能体可单独配置**默认模型**（"配置默认模型和权限，这些设置用于该智能体的新任务和协作执行"）。所以异构条件在 UI 层是**可满足**的。

- **实现者族**（lead / backend / frontend / devops）：从 §2 抄录的可见清单里选**一个**模型，四者用同一个（或同家族）。原则：优先订阅内、倍率低、上下文够。
- **tester**：必须选一个与实现者族**厂商/家族不同**的模型。判定依据是实际运行所用模型家族，不是角色名、不是会话 ID、不是界面标签。
- 如果你的订阅里**找不到**可用的异构模型：记为**模型阻塞**，不要硬凑同族模型冒充异构，也不要开付费探针去试。记法：`模型阻塞：可见清单内无第二个厂商/家族模型`。
- 其余五个角色（architect / security / researcher / design / smartcontract）建的时候按上表同法：实现者族除外的按需选，security 必须异构族。

### 3.4 工具权限三桶映射全表（十个角色）

| 角色 | 允许（免审批） | 禁止 | 其余（走审批） |
|---|---|---|---|
| arvino-lead | Read, Grep, Glob | — | Edit, Write, Bash |
| arvino-architect | Read, Grep, Glob | — | Edit, Write, Bash |
| arvino-backend | Read, Grep, Glob | — | Edit, Write, Bash |
| arvino-frontend | Read, Grep, Glob | — | Edit, Write, Bash |
| arvino-devops | Read, Grep, Glob | — | Edit, Write, Bash |
| arvino-tester | Read, Grep, Glob | Edit, Write, NotebookEdit | Bash |
| arvino-security | Read, Grep, Glob | Edit, Write, NotebookEdit | Bash |
| arvino-researcher | Read, Grep, Glob, WebSearch, WebFetch | — | Edit, Write |
| arvino-design | Read, Grep, Glob | — | Edit, Write |
| arvino-smartcontract | Read, Grep, Glob | — | Edit, Write, Bash |

映射依据 `TEAM.md` §3：researcher 的联网检索限定在"任务需要、现有工具及副作用核查通过"的范围；tester/security 的隔离默认离线；smartcontract 的合约动作按预算与安全审查执行并留痕，工具权限**不能**替代模拟、门禁与回滚要求。

> 界面上的工具名若与上表略有出入（例如搜索类工具叫 Search、终端叫 Terminal），按语义对应即可，但要在证据里记录**界面上的实际名称**。

### 3.5 保存前自查

每个智能体保存前念一遍：

- [ ] 名称 = `roles.yaml` 里的 `name`，一字不差
- [ ] 工作指令是原文照抄，没被自己改写
- [ ] tester / security 的禁止列表含 Edit 与 Write
- [ ] 默认模型已选，且 tester 家族 ≠ 实现者家族
- [ ] 没开 Full Access

**每存一个，立刻点开读回一遍**，确认显示内容与预期一致再存下一个。存完把五个智能体的最终配置快照（名称 / 说明 / 默认模型 / 允许列表 / 禁止列表）记到 §9 的证据文件里。

---

## 4. 第三步：创建智能体团队

入口：**设置 > 智能体团队 > 创建智能体团队 > 从空白创建**。

| 字段 | 填什么 |
|---|---|
| 名称 | `Arvino 研发交付组`（**创建后不可改**，想清楚再填） |
| 说明 | `Arvino 平台的受控试点团队：1 名 Qoder 总监 + 4 名执行成员，十专业职责按阶段轮换；当前仅限只读与候选快照演练。` |
| 协作指令 | 见 §4.1 全文，直接粘贴 |
| 团队负责人 | `arvino-lead` |
| 额外成员（最多 4） | `arvino-backend`、`arvino-frontend`、`arvino-devops`、`arvino-tester` |

选为负责人的智能体会自动从额外成员里移除——所以负责人选 lead 之后，成员列表里就不会再有它了，这是正常现象。

### 4.1 团队协作指令（直接粘贴这段）

```
本团队是 Arvino 平台的受控试点团队，负责人是 Qoder 总监 arvino-lead，成员是 arvino-backend、arvino-frontend、arvino-devops、arvino-tester。团队现有 5 个席位，平台共十个专业职责（architect、researcher、design、security、smartcontract 等），按阶段轮换进出席位，不因席位上限合并职责。

分工：总监只编排，负责拆派、追踪、决策与汇总汇报，不亲自实现代码、部署配置或测试修复；backend/frontend/devops 负责各自范围内的实现与自测；tester 独立验收，不参与实现、不修改被测实现与门禁。

权威依据：本团队一切行为以仓库内 contrib/qoder-agent-team/TEAM.md 与 roles.yaml 为准；两者与本指令冲突时以仓库文件为准。

当前授权（2026-10-21 收窄后）：唯一红线是**不泄密/不输出密钥**。业务写任务、生产变更、付费、commit/push/merge 按总监派单执行，不再逐条审批；执行时保留 CI/QA/安全门禁、版本绑定、回滚与变更记录。

执行规则：
一、每个任务只有一个 owner；拆分出的子任务各自有唯一 owner；同一仓库最多一个实现者并发写，frontend/backend/devops 不得同时改同一工作区。
二、三档流程：Fast 为低风险小改动，仍需适用 CI/OPA/已有策略门禁 + 独立 tester + 必要 security；Standard 为一般功能，必须独立 tester 及 security；Governed 覆盖架构、基础设施、迁移、权限、合约、费用与发布，必须方案留证加总监发布决策。三档都不省略独立测试，开发者自测不等于独立验收。
三、实现者交接必须带候选版本标识（commit 或未提交快照指纹）、改动文件绝对路径、适用 CI/OPA/已有策略门禁结果及适用性理由、已执行命令与退出码、未验证项、QA 与安全证据、风险与回退方案。代码交付必须给实际 PR 链接，且 CI、QA、安全证据与 PR head SHA 一致；不再存在“没有 commit/push 授权”的默认状态，不伪造 PR、SHA 或已通过的门禁。
四、验收者核对同一候选版本和门禁结果，只在隔离副本中运行已核查副作用的测试；不改被测实现、被测测试或门禁来让结果通过。
五、独立验收要求不同执行会话、未参与被验实现、证据绑定同一候选版本，且 tester 与 security 的实际运行模型家族分别不同于实现者。不同角色名或不同会话 ID 不能作为异构证据；默认同模型子智能体不满足异构条件。
六、变更记录绑定具体动作、项目与目标环境、commit 或 image digest（未提交则锁定候选快照指纹）、QA 与安全证据、风险与回退、执行者与时间。版本、镜像、目标环境、动作参数或风险证据变化后重新验证，不复用旧结论。除唯一红线外不再逐条审批。
七、智能合约的编译、测试、模拟、RPC 读取或探针、签名、授权、部署、转账、升级等动作按预算与安全审查执行并留痕，不再逐动作人工审批；唯一红线是不泄露私钥/助记词/密钥。
八、同一错误签名的同型失败 3 次后停止重试并交总监升级；不同问题分开计数。
九、唯一红线：不泄露/不输出密钥。其余动作（含 commit/push、合并、部署、生产写、付费）按任务目标执行并留证；工具提示词不是沙箱，Bash 与测试脚本仍可能写文件、访问网络或产生费用，执行前确认副作用。
十、汇报纪律：只在出现有效新证据时汇报，先给结论，再给绝对路径、命令结果与退出码、候选版本、未验证项、阻塞类别与解除条件、需拍板事项；没有新证据不定时刷屏，不把会话启动当业务进展。
```

---

## 5. 第四步：启用团队

- 在智能体团队列表里打开 `Arvino 研发交付组` 的开关。
- **保持设备在线**（团队任务在当前设备执行）。
- 记录开关状态截图或读回文本。

---

## 6. 第五步：只读路由验收（通过判据）

这是把 `registration_status` 从 `prepared_not_registered` 改掉的前置条件之一。**全程只读，不写业务仓、不改业务代码。**

| # | 动作 | 判据 |
|---|---|---|
| V1 | 左侧导航「新的任务」→ 点顶部 Qoder 名称或下拉箭头 → 找到 Agent Team 分组 → 选 `Arvino 研发交付组` | 页面顶部 Qoder 已变为团队名 |
| V2 | 派一条只读任务，例如：`读取本仓库 README.md，列出仓库顶层目录结构。不要修改、创建或删除任何文件，不要运行会写入的命令。` | 任务成功完成 |
| V3 | 核对任务日志 | 负责人被选中；至少一个成员被激活；各成员显示为各自智能体，不是全部同一个 |
| V4 | 工作区零改动 | 任务前后 `git status --porcelain` 计数一致，无新增条目 |
| V5 | 异构判定 | 记录本次任务里**实现侧**与**验收侧**各自实际使用的模型及其厂商/家族；两者不同则通过，相同则记模型阻塞 |
| V6 | 落盘证据 | 写 §9 的 JSON，结论字段只允许 `passed` / `passed_with_blockers` / `blocked` |

V4 的核对命令（只读）：

```bash
cd /Users/arvin/Documents/arvino/ai-devsecops-platform && git status --porcelain=v1 | wc -l
```

> V4 只保证"这次只读任务没改仓库"。它**不**证明工具层面的强制隔离——Qoder 官方文档未提供逐智能体文件路径白名单，文件可达性取决于全局权限模式与逐命令审批。这一点在证据里如实写，别写成"已强制隔离"。

---

## 7. security 怎么办（首批席位里没有它）

首批 5 席不含 security。按 `TEAM.md` §3 的"按阶段调用独立角色/会话"路径处理：

1. 由总监开一个**独立会话**承载 arvino-security，工作指令同样抄 `arvino-security.md` 正文，工具权限按 §3.4（禁止 Edit/Write）。
2. 在该独立会话里选一个与实现者**不同厂商/家族**的模型。
3. 证据同时记三样：实现者会话所用模型、验收者会话所用模型、两者家族不同的来源证据。

这样"不同执行会话"与"异构模型家族"两个条件**同时**满足，且不占团队席位、不与实现者合并。

> 官方文档未说明能否把独立会话作为团队成员加入团队，因此本卡按"独立会话 + 独立模型"设计。若你实查后发现 UI 支持逐成员模型绑定且团队内可放 security，可把 security 换入席位并把上条作废——但要在证据里写明依据。

---

## 8. 席位轮换表（十职责怎么用满五个位）

成员可通过团队列表里的编辑按钮调整（官方文档确认可改负责人和成员），所以是轮换而不是重建团队。

| 阶段 | 负责人 | 4 个成员席位 | 进入条件 |
|---|---|---|---|
| S0 就绪 | lead | backend, frontend, devops, tester | 本卡完成 + §6 只读验收通过 |
| S1 安全补位 | lead | backend, frontend, devops, security | tester 换出；tester 角色由 S0 团队承担，安全结论仍须独立给出 |
| S2 前置阶段 | lead | architect, researcher, design + 1 执行位 | 需求澄清/调研/设计阶段，架构师固化方案与验收 |
| S3 合约阶段 | lead | smartcontract, backend, devops, security | Governed 档，方案留证 + 总监发布决策 |

轮换只改团队成员配置，**不改** `roles.yaml` 与角色文件；两个 legacy 文件（`arvino-developer` / `arvino-sre` / `arvino-reviewer`）**不要创建、不要派发**。

---

## 9. 证据文件（执行完写这份）

写到 `contrib/qoder-agent-team/registration-verification.json`：

```json
{
  "schema_version": 1,
  "artifact_kind": "registration_verification_evidence_not_approval_not_task_ledger",
  "verified_at": "<填写>",
  "operator": "Arvin（GUI 手工操作）",
  "channel": "manual_gui",
  "preconditions": {
    "device_online": true,
    "full_access_enabled": false,
    "login_or_subscription_changed": false,
    "paid_model_probe_run": false
  },
  "model_catalog_visible": ["<从设置>模型抄录的可见清单>"],
  "agents": [
    {
      "name": "arvino-lead",
      "description": "<界面读回>",
      "default_model": "<界面读回>",
      "allowed_tools": ["Read", "Grep,", "Glob"],
      "denied_tools": [],
      "other_tools": ["Edit", "Write", "Bash"],
      "readback_verified": true
    }
  ],
  "team": {
    "name": "Arvino 研发交付组",
    "lead": "arvino-lead",
    "members": ["arvino-backend", "arvino-frontend", "arvino-devops", "arvino-tester"],
    "enabled": true,
    "device_online_required": true
  },
  "heterogeneous_model_evidence": {
    "implementer_model": "<实际运行模型>",
    "implementer_family": "<厂商/家族>",
    "acceptor_model": "<实际运行模型>",
    "acceptor_family": "<厂商/家族>",
    "different_families": true,
    "source": "<证据来源>"
  },
  "read_only_routing_check": {
    "task_id": "<>",
    "members_activated": ["<>"],
    "workspace_porcelain_before": 4,
    "workspace_porcelain_after": 4,
    "no_new_changes": true
  },
  "not_verified": [
    "逐智能体文件路径强制隔离（官方文档未提供该 UI 字段）",
    "业务写任务端到端",
    "旧 Hermes 调度无双调度证明"
  ],
  "conclusion": "passed | passed_with_blockers | blocked"
}
```

---

## 10. 明确不做

- 不开启 Full Access，不改登录与订阅，不索取或打印密钥。
- 不跑业务模型探针，不新增付费模型或连接器。
- 不写业务仓库，不 commit/push/merge。
- 不动、不停、不改旧 Hermes 调度。
- 不因为注册成功就认为可以写业务仓——业务写任务的闸门在 `takeover-approval-request.md` 的方案批准。
- 不修改 `TEAM.md`、`roles.yaml`、十个角色文件、`AGENTS.md`、项目章程、`SYSTEM_STATUS.md`（台账更新由总监统一做）。

---

## 11. 一个必须知道的安全事实

旧 Hermes 调度里，**只有** 一个 job 对 arvino 看板调 `hermes kanban dispatch`（`0c82db48c322`，每 15 分钟）。但另外**两个 firebird 值守 job 直接改 firebird-sea 仓库**，不走看板：

- `1ee96fd19f7f`「firebird PR 审核与 BMAD 有序推进」——**每 3 分钟**一次，审核并 squash merge PR，必要时用 worktree 修复重推。
- `1972eac66c26`「firebird 夜间流程推进」——每晚 23:30 / 02:30 / 05:30，推进 PR、合并、回写状态。

它们不经过 arvino 看板，所以"看板 running=0"的双调度检测**覆盖不到它们**。结论：

> **firebird-sea 仓库的写任务，在上述两个值守 job 停领或收窄为只读之前，保持阻塞。**

该并发写者风险已记入 `takeover-approval-request.md` §7.4 与 `TEAM.md` §1.2。**在停旧调度前，不得向 firebird-sea 派发业务写任务**；只读操作与 ai-devsecops-platform 本仓范围内的开发不受此限。停/改旧调度属总监可决策动作，执行前保留停止证据与回滚方式。
