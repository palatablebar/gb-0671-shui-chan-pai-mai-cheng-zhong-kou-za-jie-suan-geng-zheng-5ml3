import pytest

from app.errors import BusinessError
from app.state import seed_workbench


def test_seed_has_frozen_original_batch_and_one_return(wb):
    batch = wb.batches['B20261117-01']
    assert batch.frozen is True
    assert batch.status in ('已发出', '部分退回')
    # 退回票 P0002 已释放占用、待付余额恢复
    bill = wb.bills['P0002']
    assert bill.outstanding > 0
    assert bill.open_batch_no == ''
    assert any(p['result'] == '退回' for p in bill.payments)
    assert 'RBK-20261118-007' in wb.reports


def test_frozen_batch_cannot_be_modified(wb):
    # 已发出批次上再次构建/改写都被拒绝
    with pytest.raises(BusinessError) as ei:
        wb.issue_batch('B20261117-01')
    assert ei.value.code == 'BATCH_FROZEN'
    # 直接篡改条目的金额也无法通过重新发出：批次已是 frozen
    batch = wb.batches['B20261117-01']
    original_total = batch.total
    with pytest.raises(BusinessError):
        wb.issue_batch(batch.batch_no)
    assert batch.total == original_total  # 金额原样保留


def test_return_does_not_rewrite_original_batch_amount(wb):
    batch = wb.batches['B20261117-01']
    items_before = [dict(i) for i in batch.items]
    total_before = batch.total
    seal_before = batch.seal
    # 退回发生后，原批次条目、总额、封存指纹全部不变，仅状态流转
    assert batch.status == '部分退回'
    assert batch.items == items_before
    assert batch.total == total_before
    assert batch.seal == seal_before        # 回单与账面批次可永久对上


def test_duplicate_return_report_is_idempotent(wb):
    report_no = 'RBK-20261118-007'
    bill = wb.bills['P0002']
    traces_before = len(bill.payments)
    # 用相同回报号、相同票面再执行一次
    result = wb.register_return(report_no, 'B20261117-01', 'P0002',
                                '8600.50', '2026-11-18', 'ACCOUNT_MISMATCH')
    assert result['idempotent'] is True
    assert len(wb.reports) == 1                      # 不新增回报
    assert len(bill.payments) == traces_before       # 不新增付款/退回轨迹
    # 回报号数量保持 1，杜绝重复付款
    assert sum(1 for _ in wb.reports) == 1


def test_return_amount_must_match_frozen_face(wb):
    with pytest.raises(BusinessError) as ei:
        wb.register_return('RBK-BAD', 'B20261117-01', 'P0001',
                           '9999.99', '2026-11-18', 'BANK_REJECT')
    assert ei.value.code == 'AMOUNT_MISMATCH'


def test_recovery_is_separate_list_and_batch_not_merged_back(wb):
    report_no = 'RBK-20261118-007'
    original = wb.batches['B20261117-01']
    original_item_count = len(original.items)

    wb.build_recovery('RC-001', [report_no])
    rec = wb.recoveries['RC-001']
    assert rec.status == '草稿'
    assert rec.total == '8600.50'

    batch = wb.issue_recovery('RC-001', 'B20261118-R1')
    assert batch['kind'] == '恢复批次'
    assert batch['frozen'] is True
    assert batch['source_batches'] == ['B20261117-01']
    assert batch['source_reports'] == [report_no]
    # 原批次条目数与金额不变，没有被并回改写
    assert len(original.items) == original_item_count
    # 回报已标记续结
    assert wb.reports[report_no].recovered_by == 'B20261118-R1'
    # 票据续结链同时含原始退回与恢复在途
    lineage = wb._lineage(wb.bills['P0002'])
    assert 'B20261117-01' in lineage and 'B20261118-R1' in lineage and '退回' in lineage


def test_already_recovered_report_cannot_be_reused(wb):
    wb.build_recovery('RC-001', ['RBK-20261118-007'])
    wb.issue_recovery('RC-001', 'B20261118-R1')
    with pytest.raises(BusinessError) as ei:
        wb.build_recovery('RC-002', ['RBK-20261118-007'])
    assert ei.value.code == 'ALREADY_RECOVERED'


def test_bill_cannot_be_double_batched_while_inflight(wb):
    # P0001 在途，未回报前不能再编入新批次
    with pytest.raises(BusinessError) as ei:
        wb.build_batch('B-DUP', 'D20261117', ['P0001'])
    assert ei.value.code == 'BILL_IN_FLIGHT'


def test_workbench_distinguishes_recovered_vs_open(wb):
    # 初始：P0002 退回未续结 -> 仍未付清；其余未编批票也未付清
    view = wb.workbench_view()
    assert view['metrics']['仍未付清'] >= 5
    assert view['metrics']['已续结'] == 0
    # 完成续结并确认成功付款
    wb.build_recovery('RC-001', ['RBK-20261118-007'])
    wb.issue_recovery('RC-001', 'B20261118-R1')
    wb.confirm_payment('B20261118-R1', 'P0002')
    view2 = wb.workbench_view()
    p2 = next(r for r in view2['rows'] if r['bill_no'] == 'P0002')
    assert p2['outstanding'] == '0.00'
    assert p2['recovered'] is True
    assert view2['metrics']['已续结'] == 1


def test_outstanding_identity_and_reconcile_balanced(wb):
    rec = wb.reconcile()
    assert rec['pay_identity_ok'] is True
    # 存在草稿前（仅种子）：无草稿恢复清单，但有未续结回报不阻断；对账应平
    assert all(c['ok'] for c in rec['checks'])


def test_archive_blocked_while_draft_recovery_open(wb):
    # 先造一张草稿恢复清单，归档必须被阻断
    wb.build_recovery('RC-DRAFT', ['RBK-20261118-007'])
    with pytest.raises(BusinessError) as ei:
        wb.archive()
    assert ei.value.code == 'ARCHIVE_BLOCKED'
    assert wb.archives == []  # 没有产生任何归档版本


def test_full_happy_path_archive_versions_append(wb):
    # 1) 退回款续结
    wb.build_recovery('RC-001', ['RBK-20261118-007'])
    wb.issue_recovery('RC-001', 'B20261118-R1')
    wb.confirm_payment('B20261118-R1', 'P0002')
    # 2) 把其余待付票编批、发出、确认成功，全部付清
    rest = ['P0005', 'P0006', 'P0007', 'P0008']
    wb.build_batch('B20261118-02', 'D20261117', rest)
    wb.issue_batch('B20261118-02')
    for b in rest:
        wb.confirm_payment('B20261118-02', b)
    # 3) 在途的原始批次其余票也确认
    for b in ['P0001', 'P0003', 'P0004']:
        wb.confirm_payment('B20261117-01', b)

    rec = wb.reconcile()
    assert rec['balanced'] is True and rec['pay_identity_ok'] is True
    v1 = wb.archive('首次封存')
    v2 = wb.archive('再次封存')
    assert v1['version'] == 1 and v2['version'] == 2   # 版本追加不覆盖
    assert v1['seal'] != v2['seal'] or v1['version'] != v2['version']
    assert all(b.status == '已归档' for b in wb.batches.values())
    # 所有票付清
    assert all(b.outstanding == 0 for b in wb.bills.values())
