"""工作台装配：从 SQLite 快照重建 Workbench，或用样例播种一个新工作台。"""
import json
from decimal import Decimal

from app.config import ROOT
from app.domain.models import Bill, PaymentBatch, ReturnReport, RecoveryList
from app.domain.service import Workbench


def _rebuild(snapshot):
    wb = Workbench()
    wb.dockets = snapshot.get('dockets', {})
    wb.archives = snapshot.get('archives', [])
    wb.event_log = snapshot.get('event_log', [])
    wb.seq = snapshot.get('seq', 0)
    wb.revision = snapshot.get('revision', 1)
    for key, d in snapshot.get('bills', {}).items():
        bill = Bill(bill_no=d['bill_no'], vendor=d['vendor'], docket_no=d['docket_no'],
                    settle_date=d['settle_date'], due_amount=d['due_amount'],
                    paid_amount=d.get('paid_amount', '0.00'),
                    payments=d.get('payments', []),
                    open_batch_no=d.get('open_batch_no', ''))
        wb.bills[key] = bill
    for key, d in snapshot.get('batches', {}).items():
        wb.batches[key] = PaymentBatch(**{k: d.get(k) for k in (
            'batch_no', 'kind', 'docket_no', 'status', 'frozen', 'items', 'total',
            'source_batches', 'source_reports', 'recovery_of', 'seq', 'seal')})
    for key, d in snapshot.get('reports', {}).items():
        wb.reports[key] = ReturnReport(**{k: d.get(k) for k in (
            'report_no', 'batch_no', 'bill_no', 'amount', 'bank_date',
            'reason', 'recovered_by', 'seq')})
    for key, d in snapshot.get('recoveries', {}).items():
        wb.recoveries[key] = RecoveryList(**{k: d.get(k) for k in (
            'recovery_no', 'docket_no', 'status', 'items', 'total',
            'issued_batch_no', 'seq', 'src_batches', 'src_reports')})
    return wb


def seed_workbench():
    """按样例播种：1 张已发出冻结批次（含一笔隔日退回）+ 4 张待编批票据。"""
    data = json.loads((ROOT / 'sample_data' / 'seed.json').read_text())
    wb = Workbench()
    wb.load_docket(data['docket_no'], data['settle_date'], data['bills'])
    cmd = data['seed_commands']
    wb.build_batch(cmd['issued_batch'], data['docket_no'], cmd['issued_bills'])
    wb.issue_batch(cmd['issued_batch'])
    # 其余票保持待付，供工作台演示编批
    r = cmd['return_report']
    returned_bill = next(b for b in data['bills'] if b['bill_no'] == r['bill_no'])
    wb.register_return(r['report_no'], r['batch_no'], r['bill_no'],
                       returned_bill['due_amount'], r['bank_date'], r['reason'])
    return wb
