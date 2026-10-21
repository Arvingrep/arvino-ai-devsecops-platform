# 活库迁移 Runbook（c3d4e5f6a7b8）

日期：2026-10-21
执行者：总监（Qoder）
状态：**已于 2026-10-21 09:45 执行并验收通过**；旧镜像未变，回滚路径保留
范围：仅执行数据库迁移，不部署新镜像、不改 Hermes、不写业务仓库

## 1. 事实基线（已只读核实）

| 项 | 实测值 |
|---|---|
| 活库版本 | `alembic_version = b1c2d3e4f5a6` |
| 目标版本 | `c3d4e5f6a7b8`（本次迁移的 down_revision 正好是当前版本） |
| 表数量 | 11 张（public） |
| 迁移内容 | `steps` 加 5 个可空列；新增 `repo_write_locks` 表与索引 |
| 对旧镜像影响 | 旧镜像 `3c9be880517a` 不读取新列/新表，属向后兼容的加列加表 |
| 克隆验证 | 用活库 schema-only dump 在临时 Postgres 16 上完成 `upgrade → downgrade → upgrade`，全部通过 |
| 回滚 | `alembic downgrade -1`，已验证列与表归零 |
| 执行结果 | `alembic upgrade head` exit 0；`alembic_version = c3d4e5f6a7b8`；5 个 `executor_*` 列 + `repo_write_locks` 表就位；关键表行数不变；pods 0 新增重启；`/healthz` 200 |

## 2. 执行前检查（只读）

1. `kubectl -n arvino get pods`：api/worker/opa/postgres 全部 Ready，0 重启。
2. 记录关键表行数：projects/issues/workflow_runs/steps/events/approvals。
3. 记录 `alembic_version`。
4. 生成 schema-only 备份到本地临时文件（不含业务数据），记录 sha256；不提交、不外传。
5. 确认候选 worktree 在 `e8fa6e6`，`alembic heads` 只有 `c3d4e5f6a7b8`。

## 3. 执行（保留备份与回滚）

```bash
# 1) 端口转发（只监听本机）
kubectl -n arvino port-forward svc/arvino-postgres 55435:5432

# 2) 从 secret 读取口令，不打印
export PGPASSWORD="$(kubectl -n arvino get secret arvino-db-secret -o jsonpath='{.data.password}' | base64 -d)"
export DATABASE_URL="postgresql+psycopg://arvino:${PGPASSWORD}@127.0.0.1:55435/arvino"

# 3) 在候选 worktree 执行
cd .worktrees/t1-step-leases
PYTHONPATH=$PWD/src .venv/bin/python -m alembic upgrade head
```

约束：单条命令、`statement_timeout=30s`；失败立即停止并回滚，不重试第二次。

## 4. 执行后验收

1. `alembic_version = c3d4e5f6a7b8`。
2. `steps` 存在 5 个 `executor_*` 列；`repo_write_locks` 表存在。
3. projects/issues/workflow_runs/steps/events/approvals 行数与执行前一致。
4. `kubectl -n arvino get pods` 全部 Ready，0 新增重启；`/healthz` 200。
5. 旧镜像继续忽略新列/新表；不触发任何租约逻辑。

## 5. 回滚

```bash
# 同一端口转发与 DATABASE_URL
PYTHONPATH=$PWD/src .venv/bin/python -m alembic downgrade -1
```

验收：版本回到 `b1c2d3e4f5a6`，5 列与 `repo_write_locks` 归零；pods 健康；行数不变。

## 6. 明确不在本次范围

- 不部署候选镜像（B3/harness/租约代码仍只在候选分支）。
- 不停止/修改 Hermes。
- 不写任何业务仓库。
- 不做生产流量切换。

## 7. 执行记录

- 时间：2026-10-21 09:45（Asia/Shanghai）
- 前置：worktree HEAD `e8fa6e6`；`alembic heads` 唯一 `c3d4e5f6a7b8`；活库 `alembic_version = b1c2d3e4f5a6`；public 表 11 张；关键表行数基线已记录；pods 全 Running 0 重启。
- 备份：schema-only dump（16993 字节，11 张 CREATE TABLE、0 行业务数据），sha256 `953f113fcac834f63bcd7c2b701217cd587b416f7b0cb8582555cdd6628a6efd`；仅本地临时文件，不含业务数据。
- 执行：`kubectl --context gcp-gke -n arvino port-forward svc/arvino-postgres 55435:5432`；口令经 `PGPASSWORD` 传入，不打印、不写入连接串；`PGOPTIONS="-c statement_timeout=30000 -c lock_timeout=10000"`；`alembic upgrade head` → exit 0，日志 `Running upgrade b1c2d3e4f5a6 -> c3d4e5f6a7b8, step_leases`。
- 验收：版本 `c3d4e5f6a7b8`；5 列全部 nullable；`repo_write_locks` + 索引存在；public 表 11→12；六张关键表行数与基线一致；pods 全 Running 0 新增重启；`/healthz` 200。
- 回滚：未触发；`alembic downgrade -1` 保持可用且已在克隆库验证。
- 未执行：部署候选镜像、B3 开关、停/改 Hermes、业务仓库写、流量切换。
