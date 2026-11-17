# 出纳付款批次与退回续结工作台

资金出纳处理日结后未付票的付款编批、银行隔日退回回报、退回款续结、批次对账与归档封存。

## 核心业务裁决

**已发出付款批次一旦冻结即不可改写；银行退回款不并回原日清单、不改写原批次，而是另成“恢复清单”，发出为独立的“恢复批次”续付。**

这样付款回单与账面批次永久对得上：原批次的条目、金额、封存指纹在退回前后完全不变，只流转状态；退回与续付通过票据上的“续结链（lineage）”逐笔可追溯。

## 七条不变量

1. **批次冻结**：批次发出（`issue`）即 `frozen=true` 并生成封存指纹；任何改写返回 `409 BATCH_FROZEN`。
2. **退回不改原批**：退回只把票据在途轨迹转为“退回”并登记回报，原批次条目与总额原样保留。
3. **回报幂等**：同一银行回报号重复执行返回 `idempotent=true` 的既有记录，绝不重复生成付款（应用层 + DB 唯一约束双保险）。
4. **待付守恒**：`待付余额 = 应付 − 成功付款`，恒非负；在途批次占用待付，未回报前不可重复编批。
5. **恢复独立**：恢复清单只汇集“未续结”回报；已续结回报再用被 `ALREADY_RECOVERED` 拒绝，杜绝重复续付。
6. **对账一致才能归档**：归档前校验金额守恒、在途占用一致、退回回报与轨迹一致、无草稿清单；不平返回 `409 ARCHIVE_BLOCKED`。
7. **归档事务留旧版**：快照与归档版本同事务写入，失败整体回滚；归档版本只追加不覆盖，旧批次与旧版清单保留。

## 模块与 API

| 模块 | 页面 | 主要 API |
|---|---|---|
| 日结清单 | `GET /dockets` | `POST /api/dockets` |
| 付款批次 | `GET /batches` | `POST /api/batches`、`.../issue`、`.../confirm` |
| 退回回报 | `GET /returns` | `POST /api/returns`（幂等） |
| 恢复续结 | `GET /recovery` | `POST /api/recovery`、`.../issue` |
| 续结工作台 | `GET /workbench` | `GET /api/workbench` |
| 批次对账 | `GET /reconcile` | `GET /api/reconcile` |
| 归档封存 | `GET /archive` | `POST /api/archive` |

## 运行

```sh
pip install -r requirements.txt
pytest -q
uvicorn app.main:app --port 18241
# 或
docker compose up --build -d
```

浏览器访问 http://localhost:18241 。首次启动用 `sample_data/seed.json` 播种：一张已发出冻结批次 `B20261117-01`，其中 `P0002` 已收到隔日退回回报 `RBK-20261118-007`，另有 4 张票待编批。

## 典型续结链路

```
P0002  B20261117-01[原始批次·退回·RBK-20261118-007] → B20261118-R1[恢复批次·成功]
```

代码在 `app/`，核心规则在 `app/domain/service.py`，持久化事务在 `app/repository.py`，测试在 `tests/`。不连接真实银行通道，`confirm` 仅用于模拟银行成功回单。
