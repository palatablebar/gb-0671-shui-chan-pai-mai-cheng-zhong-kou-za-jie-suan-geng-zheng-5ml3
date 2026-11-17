# 深度登记 · 出纳付款批次与退回续结

规模：中型。使用者：资金出纳。核心裁决：**已发出批次冻结不可改写；退回款另成恢复清单续付，不并回原日清单。**

## 模块与测试映射

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 日结清单 | 导入日结后未付票 | `POST /api/dockets` | `app/modules/dockets.py`、`service.load_docket` | `test_service.py`、`test_invariants.py::test_docket_rejects...` |
| M2 付款批次 | 按现行生效结果编批并发出 | `POST /api/batches`、`.../issue` | `service.build_batch/issue_batch` | `test_service.py::test_bill_cannot_be_double_batched...` |
| M3 退回回报 | 登记银行隔日退回（幂等） | `POST /api/returns` | `service.register_return`、`repository.remember_report` | `test_service.py::test_duplicate_return...`、`test_invariants.py::test_db_idempotency...` |
| M4 恢复续结 | 退回款另成恢复清单并发出恢复批次 | `POST /api/recovery`、`.../issue` | `service.build_recovery/issue_recovery` | `test_service.py::test_recovery_is_separate...` |
| M5 续结工作台 | 看清已续结/仍未付清与续结链 | `GET /api/workbench` | `service.workbench_view/_lineage` | `test_service.py::test_workbench_distinguishes...` |
| M6 批次对账 | 归档前一致性校验 | `GET /api/reconcile` | `service.reconcile` | `test_service.py::test_outstanding_identity...` |
| M7 归档封存 | 事务归档，失败留旧版 | `POST /api/archive` | `service.archive`、`repository.save` | `test_service.py::test_archive_*`、`test_invariants.py::test_archive_failure...` |

## 不变量登记（对应业务规则）

| 编号 | 业务规则 | 落地机制 | 违反时错误码 |
|---|---|---|---|
| INV-1 | 已发出批次不可改写，回单与账面批次对得上 | `frozen` 标志 + 发出时封存指纹；条目/金额/指纹退回前后不变 | `BATCH_FROZEN`(409) |
| INV-2 | 退回不并回原日清单、不改原批次 | 仅在票据轨迹登记“退回”，原批次只流转状态；另建恢复清单 | （结构性保证） |
| INV-3 | 同一退回回报重复执行不重复付款 | 应用层按 `report_no` 去重 + DB 唯一约束双保险，命中返回 `idempotent=true` | （幂等返回） |
| INV-4 | 待付余额守恒、不可超付/重复编批 | `outstanding=due-成功付款` 恒非负；在途占用未释放前禁止再编批 | `BILL_IN_FLIGHT`、`OUTSTANDING_BROKEN` |
| INV-5 | 退回款只能续付一次 | 回报 `recovered_by` 标记，已续结再用被拒 | `ALREADY_RECOVERED` |
| INV-6 | 归档前批次与待付余额对账一致 | 四项守恒检查 + 无草稿清单 + 无未付清/在途 | `ARCHIVE_BLOCKED`(409) |
| INV-7 | 归档失败保留原批次与旧版清单 | 快照与归档版本同事务写入，失败整体回滚；版本只追加不覆盖 | `PERSIST_FAILED`(500) |

## 对账口径

- 票据守恒：每张票 `待付 = 应付 − 已付`，非负。
- 批次守恒：冻结批次 `总额 = 条目金额和`，条目不超票面。
- 在途一致：仍“在途”的付款轨迹金额合计 = 被在途占用票据的待付额（按轨迹而非批次全额，兼容部分退回）。
- 退回一致：退回回报金额合计 = 票据“退回”轨迹金额合计。
- 资金恒等：`应付合计 = 已付合计 + 待付余额合计`。

## 已知不覆盖

不连接真实银行通道：`confirm` 仅模拟银行成功回单；并发写依赖 SQLite 单写者与回报号唯一约束，未做多节点分布式锁。
