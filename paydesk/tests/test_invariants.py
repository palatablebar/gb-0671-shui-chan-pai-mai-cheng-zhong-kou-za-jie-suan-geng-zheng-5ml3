"""关键业务不变量的边界测试：幂等兜底、超付防护、归档回滚留旧版。"""
import pytest

from app.errors import BusinessError
from app.repository import Repository
from app.state import seed_workbench


def test_recovery_lineage_survives_persistence_roundtrip(tmp_path):
    from app.state import _rebuild
    wb = seed_workbench()
    wb.build_recovery('RC-1', ['RBK-20261118-007'])
    wb.issue_recovery('RC-1', 'B-R1')
    repo = Repository(str(tmp_path / 'persist.sqlite'))
    repo.save(wb.snapshot())
    rebuilt = _rebuild(repo.load_raw())
    batch = rebuilt.batches['B-R1']
    assert batch.source_batches == ['B20261117-01']
    assert batch.source_reports == ['RBK-20261118-007']
    assert rebuilt.recoveries['RC-1'].src_batches == ['B20261117-01']


def test_db_idempotency_unique_constraint_blocks_duplicate(tmp_path):
    repo = Repository(str(tmp_path / 'x.sqlite'))
    first = repo.remember_report('RBK-X', {'a': 1})
    second = repo.remember_report('RBK-X', {'a': 2})
    assert first is True and second is False      # 唯一约束兜底，重复回报不被二次接受


def test_cannot_build_batch_over_more_than_outstanding(wb):
    # P0001 在途，未回报前重复编批被拒（防止超额付款）
    with pytest.raises(BusinessError) as ei:
        wb.build_batch('B-X', 'D20261117', ['P0001'])
    assert ei.value.code == 'BILL_IN_FLIGHT'


def test_recovery_rejected_when_bill_outstanding_insufficient(wb):
    # 先把 P0002 的退回续结并确认成功（待付清零）
    wb.build_recovery('RC-1', ['RBK-20261118-007'])
    wb.issue_recovery('RC-1', 'B-R1')
    wb.confirm_payment('B-R1', 'P0002')
    assert wb.bills['P0002'].outstanding == 0
    # 回报已续结，再次汇集必被拒
    with pytest.raises(BusinessError) as ei:
        wb.build_recovery('RC-2', ['RBK-20261118-007'])
    assert ei.value.code == 'ALREADY_RECOVERED'


def test_docket_rejects_non_positive_and_duplicates(wb):
    result = wb.load_docket('D-EXTRA', '2026-11-18', [
        {'bill_no': '', 'vendor': '无名', 'due_amount': '100'},
        {'bill_no': 'NEG', 'vendor': '负数', 'due_amount': '-50'},
        {'bill_no': 'P0001', 'vendor': '已存在', 'due_amount': '100'},
        {'bill_no': 'NEW1', 'vendor': '新票', 'due_amount': '200.00'},
    ])
    codes = {c['code'] for c in result['conflicts']}
    assert {'MISSING_BILL_NO', 'BAD_AMOUNT', 'BILL_EXISTS'} <= codes
    assert result['loaded'] == ['NEW1']


def test_archive_failure_preserves_prior_version(tmp_path):
    # 先成功归档一版
    wb = seed_workbench()
    wb.build_recovery('RC-1', ['RBK-20261118-007'])
    wb.issue_recovery('RC-1', 'B-R1')
    wb.confirm_payment('B-R1', 'P0002')
    for b in ['P0001', 'P0003', 'P0004']:
        wb.confirm_payment('B20261117-01', b)
    rest = ['P0005', 'P0006', 'P0007', 'P0008']
    wb.build_batch('B-02', 'D20261117', rest)
    wb.issue_batch('B-02')
    for b in rest:
        wb.confirm_payment('B-02', b)

    repo = Repository(str(tmp_path / 'arc.sqlite'))
    v1 = wb.archive('v1')
    repo.save(wb.snapshot(), archived_version=v1)
    assert repo.archive_history() and repo.archive_history()[0]['version'] == 1

    # 制造一个对账不平的工作台：直接破坏某张票的已付金额，使待付守恒失效
    broken = seed_workbench()
    broken.build_recovery('RC-1', ['RBK-20261118-007'])
    broken.issue_recovery('RC-1', 'B-R1')
    broken.confirm_payment('B-R1', 'P0002')
    broken.bills['P0002'].paid_amount = '999999.99'   # 人为制造不平衡
    rec = broken.reconcile()
    assert rec['balanced'] is False
    with pytest.raises(BusinessError) as ei:
        broken.archive('should-fail')
    assert ei.value.code == 'ARCHIVE_BLOCKED'
    # 既有的 v1 归档版本原样保留，未追加新版本
    assert len(repo.archive_history()) == 1
    assert repo.archive_history()[0]['version'] == 1
