# SYSTEM_STATUS.md 维护工作流

## 问题背景

SYSTEM_STATUS.md 是平台状态的单一真相源（SSOT），由总监 cron job (0c82db48c322) 每 15 分钟更新。

**历史隐患**（已于 2026-10-20 修复）：
- main 分支仅有 61 行存根版本
- 完整的 291 行巡检历史仅存在于工作区未提交修改中
- 任何 `git checkout <branch>` 会将文件打回 61 行，历史丢失
- 2026-10-20 07:00 巡检已真实发生过一次数据丢失（靠 scratch 备份恢复）

## 当前方案（2026-10-20 实施）

**Option a - 纳入版本控制（已采纳）**：

SYSTEM_STATUS.md 现作为**真正的受版本管理文件**提交进 main 分支。

### 工作流程

1. **日常更新**：总监 cron 直接编辑工作区的 SYSTEM_STATUS.md
2. **定期快照**：由 arvino-devops 角色按固定周期（建议每天一次）开 PR 合入 main
3. **Git 保护**：版本控制防止数据丢失，commit 历史提供审计价值

### Churn 评估

**结论：可接受**
- 状态台账本身设计为高频变更（每 15 分钟一次巡检）
- 每日一次 PR 的 commit 频率对于状态快照是合理的
- Git diff 清晰展示系统状态随时间的演变
- 历史记录本身具有价值（故障追溯、趋势分析）

### 示例操作

```bash
# 总监 cron 正常运行，直接编辑工作区文件
# （无需改动 cron 脚本）

# arvino-devops 角色每天一次快照到 main
cd /Users/arvin/Documents/arvino/ai-devsecops-platform
git checkout -b chore/system-status-snapshot-$(date +%Y%m%d)
git add docs/internal/SYSTEM_STATUS.md
git commit -m "chore(docs): daily SYSTEM_STATUS.md snapshot $(date +%Y-%m-%d)"
git push -u origin HEAD
gh pr create --title "chore(docs): daily SYSTEM_STATUS.md snapshot" --base main
```

## 备选方案（未采纳，留作参考）

**Option b - 迁移到仓库外**：
- 文件路径：`~/.hermes/state/arvino/SYSTEM_STATUS.md`
- 仓库内只留 README 指针
- 需修改总监 cron 脚本
- 需实现独立的每日备份机制（不能依赖 scratch，scratch 定期清理）

**未采纳原因**：
- 实现复杂度更高
- 失去 Git 历史保护
- 需要额外的备份基础设施

## 验证清单

任何时候，以下命令应证明数据已正确持久化：

```bash
cd /Users/arvin/Documents/arvino/ai-devsecops-platform

# 1. main 分支最近有针对 SYSTEM_STATUS.md 的提交
git log --oneline -3 -- docs/internal/SYSTEM_STATUS.md
# 应显示 PR#29 或更新的提交

# 2. main 分支版本行数与工作区一致（±0，或最新巡检的合理差异）
git ls-tree main docs/internal/SYSTEM_STATUS.md | awk '{print $3}' | xargs git cat-file blob | wc -l
wc -l docs/internal/SYSTEM_STATUS.md
# 两者应接近（工作区可能更新，但不应是 61 vs 291 的巨大差异）

# 3. 切换分支不会导致数据丢失
git checkout main
wc -l docs/internal/SYSTEM_STATUS.md
# 应显示完整行数（290+），而非 61 行存根
```

## 相关 PR

- **PR #29**: 初始修复 - 提交完整 291 行 SYSTEM_STATUS.md 到版本控制
  - 同时清理了仓库垃圾（.worktrees/, .dup-backup/, *.bak）
  - 纳入了 PROJECT_CHARTER.md 和运维脚本

## 变更流程约束（2026-10-21 起）

- 唯一红线：**不泄密/不输出密钥**。
- 状态文件可按本流程直接 commit/push；需要合并时由总监按任务目标决定，不再逐条等待批准。
- 生产变更保留备份、回滚与健康检查；状态记录只增不删。
- 不触碰生产数据是默认的谨慎做法，但不是需要逐条审批的红线。
