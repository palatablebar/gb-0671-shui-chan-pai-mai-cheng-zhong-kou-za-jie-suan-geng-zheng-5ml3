"""出纳付款工作台核心服务。

不变量：
1. 已发出批次冻结：frozen 批次的条目与金额永不被改写（付款回单与账面批次可永久对上）。
2. 退回不改写原批次：只登记票据轨迹“退回”+ 回报记录，原批次仅状态流转、金额保留。
3. 回报幂等：同一银行回报号重复执行只返回既有结果，绝不重复生成付款/恢复项。
4. 待付余额守恒：outstanding = due - 成功付款，恒非负；恢复批次只占用未付清余额。
5. 归档事务：对账不平/有阻断则整体回滚，旧批次与旧版清单原样保留。
"""
import hashlib
import json
from decimal import Decimal

from app.config import RETURN_REASONS, BATCH_FROZEN_STATUSES
from app.domain.models import (
    Bill, BatchItem, PaymentBatch, ReturnReport, RecoveryItem, RecoveryList,
)
from app.errors import BusinessError, require
from app.money_utils import money, D, add


class Workbench:
    def __init__(self):
        self.bills = {}                 # bill_no -> Bill
        self.dockets = {}              # docket_no -> [bill_no...]
        self.batches = {}              # batch_no -> PaymentBatch
        self.reports = {}              # report_no -> ReturnReport
        self.recoveries = {}           # recovery_no -> RecoveryList
        self.archives = []             # 归档版本列表（追加，永不覆盖）
        self.event_log = []            # 命令轨迹
        self.seq = 0
        self.revision = 1

    # ---------- 基础工具 ----------
    def _next(self):
        self.seq += 1
        return self.seq

    def log(self, action, detail):
        self.event_log.append({'seq': self._next(), 'action': action, 'detail': detail})

    def _bill(self, bill_no):
        bill = self.bills.get(bill_no)
        require(bill is not None, 'BILL_NOT_FOUND', f'票据不存在: {bill_no}', 404)
        return bill

    def _batch(self, batch_no):
        batch = self.batches.get(batch_no)
        require(batch is not None, 'BATCH_NOT_FOUND', f'批次不存在: {batch_no}', 404)
        return batch

    def _assert_not_frozen(self, batch):
        require(not batch.frozen, 'BATCH_FROZEN',
                f'批次 {batch.batch_no} 已发出并冻结，禁止改写；退回款请走恢复清单', 409)

    # ---------- M1 日结清单（导入未付票） ----------
    def load_docket(self, docket_no, settle_date, rows):
        """rows: [{bill_no, vendor, due_amount}]。同一票据重复导入按冲突隔离，不静默覆盖。"""
        seen = set()
        loaded, conflicts = [], []
        for row in rows:
            bill_no = str(row['bill_no']).strip()
            if not bill_no:
                conflicts.append({'code': 'MISSING_BILL_NO', 'detail': '票据号缺失', 'raw': row})
                continue
            amount = money(row['due_amount'])
            if D(amount) <= 0:
                conflicts.append({'code': 'BAD_AMOUNT', 'detail': f'应付须为正: {amount}', 'raw': row})
                continue
            if bill_no in seen:
                conflicts.append({'code': 'DUP_IN_DOCKET', 'detail': f'清单内重复票据 {bill_no}', 'raw': row})
                continue
            if bill_no in self.bills:
                conflicts.append({'code': 'BILL_EXISTS', 'detail': f'票据已在工作台 {bill_no}', 'raw': row})
                continue
            seen.add(bill_no)
            bill = Bill(bill_no=bill_no, vendor=str(row['vendor']).strip(),
                        docket_no=docket_no, settle_date=settle_date, due_amount=amount)
            self.bills[bill_no] = bill
            self.dockets.setdefault(docket_no, []).append(bill_no)
            loaded.append(bill_no)
        self.log('LOAD_DOCKET', {'docket_no': docket_no, 'loaded': len(loaded), 'conflicts': len(conflicts)})
        return {'docket_no': docket_no, 'loaded': loaded, 'conflicts': conflicts}

    # ---------- M2 付款批次（按现行生效结果编批） ----------
    def build_batch(self, batch_no, docket_no, bill_nos):
        require(batch_no not in self.batches, 'BATCH_EXISTS', f'批次号已存在: {batch_no}', 409)
        items, total = [], Decimal('0')
        for bill_no in bill_nos:
            bill = self._bill(bill_no)
            require(bill.docket_no == docket_no, 'DOCKET_MISMATCH',
                    f'票据 {bill_no} 不属于日清单 {docket_no}')
            outstanding = bill.outstanding
            require(outstanding > 0, 'NOTHING_DUE', f'票据 {bill_no} 已无待付余额，不可编入批次')
            require(not bill.open_batch_no, 'BILL_IN_FLIGHT',
                    f'票据 {bill_no} 已在在途批次 {bill.open_batch_no}，未回报前不可重复编批')
            items.append(BatchItem(bill_no=bill_no, vendor=bill.vendor, amount=money(outstanding)))
            total += outstanding
        require(items, 'EMPTY_BATCH', '付款批次至少包含一张票据')
        batch = PaymentBatch(batch_no=batch_no, kind='原始批次', docket_no=docket_no,
                             status='待发出', frozen=False,
                             items=[i.to_dict() for i in items], total=money(total), seq=self._next())
        self.batches[batch_no] = batch
        self.log('BUILD_BATCH', {'batch_no': batch_no, 'items': len(items), 'total': batch.total})
        return batch.to_dict()

    def issue_batch(self, batch_no):
        batch = self._batch(batch_no)
        self._assert_not_frozen(batch)
        # 再次校验每张票仍有足额待付且无在途占用
        for item in batch.items:
            bill = self._bill(item['bill_no'])
            require(bill.outstanding >= D(item['amount']), 'OUTSTANDING_CHANGED',
                    f'票据 {bill.bill_no} 待付余额已变化，需重新编批')
            require(not bill.open_batch_no, 'BILL_IN_FLIGHT',
                    f'票据 {bill.bill_no} 已被在途批次占用')
        for item in batch.items:
            bill = self._bill(item['bill_no'])
            bill.payments.append({'batch_no': batch_no, 'kind': batch.kind,
                                  'amount': item['amount'], 'result': '在途',
                                  'report_no': '', 'seq': self._next()})
            bill.open_batch_no = batch_no
        batch.status = '已发出'
        batch.frozen = True
        batch.seal = self._seal(batch)
        self.log('ISSUE_BATCH', {'batch_no': batch_no, 'total': batch.total, 'seal': batch.seal[:12]})
        return batch.to_dict()

    # ---------- M3 退回回报（隔日，幂等） ----------
    def register_return(self, report_no, batch_no, bill_no, amount, bank_date, reason):
        # 幂等：同一银行回报号重复执行，直接回既有记录，不再产生任何付款。
        existing = self.reports.get(report_no)
        if existing is not None:
            self.log('RETURN_IDEMPOTENT_HIT', {'report_no': report_no, 'batch_no': existing.batch_no})
            return {'idempotent': True, 'report': existing.to_dict()}
        require(reason in RETURN_REASONS, 'BAD_REASON', f'未知退回原因码: {reason}')
        batch = self._batch(batch_no)
        require(batch.frozen, 'BATCH_NOT_ISSUED', f'批次 {batch_no} 尚未发出，无退回可言')
        item = next((i for i in batch.items if i['bill_no'] == bill_no), None)
        require(item is not None, 'ITEM_NOT_IN_BATCH', f'票据 {bill_no} 不在批次 {batch_no} 内')
        amt = money(amount)
        require(D(amt) == D(item['amount']), 'AMOUNT_MISMATCH',
                f'退回金额 {amt} 与批次票面 {item["amount"]} 不符；冻结批次不可部分改写')
        report = ReturnReport(report_no=report_no, batch_no=batch_no, bill_no=bill_no,
                              amount=amt, bank_date=bank_date, reason=reason, seq=self._next())
        self.reports[report_no] = report

        bill = self._bill(bill_no)
        # 把该票在本批次的在途轨迹转为退回；不删除、不改写原批次条目。
        target = next((p for p in reversed(bill.payments)
                       if p['batch_no'] == batch_no and p['result'] == '在途'), None)
        require(target is not None, 'NO_INFLIGHT_PAYMENT',
                f'票据 {bill_no} 在批次 {batch_no} 没有可退回的在途付款')
        target['result'] = '退回'
        target['report_no'] = report_no
        if bill.open_batch_no == batch_no:
            bill.open_batch_no = ''      # 释放占用，待付余额重新可续付
        if batch.status == '已发出':
            batch.status = '部分退回'
        self.log('REGISTER_RETURN', {'report_no': report_no, 'batch_no': batch_no,
                                     'bill_no': bill_no, 'amount': amt, 'reason': reason})
        return {'idempotent': False, 'report': report.to_dict()}

    # ---------- M4 恢复续结（另成恢复清单，不并回原日清单） ----------
    def build_recovery(self, recovery_no, report_nos):
        """把尚未续结的退回回报汇集为一张恢复清单（草稿）。"""
        require(recovery_no not in self.recoveries, 'RECOVERY_EXISTS',
                f'恢复清单号已存在: {recovery_no}', 409)
        items, total, dockets, src_batches, src_reports = [], Decimal('0'), set(), [], []
        for report_no in report_nos:
            report = self.reports.get(report_no)
            require(report is not None, 'REPORT_NOT_FOUND', f'回报不存在: {report_no}', 404)
            require(not report.recovered_by, 'ALREADY_RECOVERED',
                    f'回报 {report_no} 已由恢复批次 {report.recovered_by} 续结，不可重复续付')
            bill = self._bill(report.bill_no)
            require(bill.outstanding >= D(report.amount), 'RECOVERY_EXCEEDS_DUE',
                    f'票据 {bill.bill_no} 待付余额不足以续付 {report.amount}')
            items.append(RecoveryItem(report_no=report_no, source_batch_no=report.batch_no,
                                      bill_no=bill.bill_no, vendor=bill.vendor, amount=report.amount))
            total += D(report.amount)
            dockets.add(bill.docket_no)
            src_batches.append(report.batch_no)
            src_reports.append(report_no)
        require(items, 'EMPTY_RECOVERY', '恢复清单至少包含一条退回回报')
        docket_no = sorted(dockets)[0]
        rec = RecoveryList(recovery_no=recovery_no, docket_no=docket_no, status='草稿',
                           items=[i.to_dict() for i in items], total=money(total), seq=self._next(),
                           src_batches=sorted(set(src_batches)), src_reports=list(src_reports))
        self.recoveries[recovery_no] = rec
        self.log('BUILD_RECOVERY', {'recovery_no': recovery_no, 'items': len(items), 'total': rec.total})
        return rec.to_dict()

    def issue_recovery(self, recovery_no, batch_no):
        rec = self.recoveries.get(recovery_no)
        require(rec is not None, 'RECOVERY_NOT_FOUND', f'恢复清单不存在: {recovery_no}', 404)
        require(rec.status == '草稿', 'RECOVERY_ALREADY_ISSUED',
                f'恢复清单 {recovery_no} 已发出，不可重复发出')
        require(batch_no not in self.batches, 'BATCH_EXISTS', f'批次号已存在: {batch_no}', 409)
        # 发出前再次核验：所有回报仍未续结、票据仍有足额待付
        for it in rec.items:
            report = self.reports[it['report_no']]
            require(not report.recovered_by, 'ALREADY_RECOVERED',
                    f'回报 {it["report_no"]} 已被续结')
            bill = self._bill(it['bill_no'])
            require(not bill.open_batch_no, 'BILL_IN_FLIGHT', f'票据 {bill.bill_no} 存在在途付款')
            require(bill.outstanding >= D(it['amount']), 'RECOVERY_EXCEEDS_DUE',
                    f'票据 {bill.bill_no} 待付余额不足')
        batch_items = [BatchItem(bill_no=i['bill_no'], vendor=i['vendor'], amount=i['amount']).to_dict()
                       for i in rec.items]
        batch = PaymentBatch(batch_no=batch_no, kind='恢复批次', docket_no=rec.docket_no,
                             status='已发出', frozen=True, items=batch_items, total=rec.total,
                             source_batches=rec.src_batches,
                             source_reports=rec.src_reports,
                             recovery_of=recovery_no, seq=self._next())
        batch.seal = self._seal(batch)
        self.batches[batch_no] = batch
        for it in rec.items:
            report = self.reports[it['report_no']]
            report.recovered_by = batch_no
            bill = self._bill(it['bill_no'])
            bill.payments.append({'batch_no': batch_no, 'kind': '恢复批次',
                                  'amount': it['amount'], 'result': '在途',
                                  'report_no': '', 'seq': self._next()})
            bill.open_batch_no = batch_no
        rec.status = '已发出'
        rec.issued_batch_no = batch_no
        self.log('ISSUE_RECOVERY', {'recovery_no': recovery_no, 'batch_no': batch_no,
                                    'items': len(batch_items), 'total': batch.total})
        return batch.to_dict()

    # ---------- 模拟银行成功回报（用于演示续付成功；真实环境由银行回单驱动） ----------
    def confirm_payment(self, batch_no, bill_no):
        batch = self._batch(batch_no)
        require(batch.frozen, 'BATCH_NOT_ISSUED', f'批次 {batch_no} 尚未发出')
        bill = self._bill(bill_no)
        target = next((p for p in reversed(bill.payments)
                       if p['batch_no'] == batch_no and p['result'] == '在途'), None)
        require(target is not None, 'NO_INFLIGHT_PAYMENT', f'票据 {bill_no} 无该批次在途付款')
        target['result'] = '成功'
        bill.paid_amount = money(D(bill.paid_amount) + D(target['amount']))
        if bill.open_batch_no == batch_no:
            bill.open_batch_no = ''
        # 批次状态：全部条目对应在途结清则置已结清
        inflight_left = any(p['result'] == '在途' for b in self.bills.values() for p in b.payments
                            if p['batch_no'] == batch_no)
        if not inflight_left and batch.status in ('已发出', '部分退回'):
            batch.status = '已结清'
        self.log('CONFIRM_PAYMENT', {'batch_no': batch_no, 'bill_no': bill_no, 'amount': target['amount']})
        return bill.to_dict()

    # ---------- M5 工作台视图 ----------
    def workbench_view(self):
        rows = []
        for bill in self.bills.values():
            d = bill.to_dict()
            d['has_return'] = bill.has_return
            d['recovered'] = bill.outstanding <= 0 and bill.has_return
            d['lineage'] = self._lineage(bill)
            rows.append(d)
        recovered = [r for r in rows if r['recovered']]
        open_rows = [r for r in rows if D(r['outstanding']) > 0]
        return {
            'rows': sorted(rows, key=lambda r: r['bill_no']),
            'metrics': {
                '票据总数': len(rows),
                '已续结': len(recovered),
                '仍未付清': len(open_rows),
                '待付余额合计': money(add(*[r['outstanding'] for r in open_rows]) if open_rows else Decimal('0')),
                '退回回报数': len(self.reports),
                '未续结回报': sum(1 for r in self.reports.values() if not r.recovered_by),
            },
        }

    def _lineage(self, bill):
        parts = []
        for p in bill.payments:
            tag = p['kind']
            if p['result'] == '退回':
                parts.append(f"{p['batch_no']}[{tag}·退回·{p['report_no']}]")
            else:
                parts.append(f"{p['batch_no']}[{tag}·{p['result']}]")
        return ' → '.join(parts) or '—'

    # ---------- M6 批次对账 ----------
    def reconcile(self):
        checks, blockers = [], []

        # 守恒1：每张票 outstanding = due - paid，且非负
        for bill in self.bills.values():
            calc = D(bill.due_amount) - D(bill.paid_amount)
            ok = calc == bill.outstanding and calc >= 0
            if not ok:
                blockers.append({'code': 'OUTSTANDING_BROKEN', 'detail': f'票据 {bill.bill_no} 待付余额不守恒'})
            checks.append({'item': f'票据 {bill.bill_no} 待付守恒', 'ok': ok,
                           'expected': money(calc), 'actual': money(bill.outstanding)})

        # 守恒2：每个冻结批次 total = 条目金额和；条目票据存在且金额不超原票面
        for batch in self.batches.values():
            item_sum = add(*[i['amount'] for i in batch.items]) if batch.items else Decimal('0')
            ok = D(batch.total) == item_sum
            checks.append({'item': f'批次 {batch.batch_no} 金额守恒', 'ok': ok,
                           'expected': money(item_sum), 'actual': batch.total})
            if not ok:
                blockers.append({'code': 'BATCH_SUM_BROKEN', 'detail': f'批次 {batch.batch_no} 总额与条目不符'})
            for it in batch.items:
                bill = self.bills.get(it['bill_no'])
                if bill is None or D(it['amount']) > D(bill.due_amount):
                    blockers.append({'code': 'ITEM_EXCEEDS_DUE',
                                     'detail': f'批次 {batch.batch_no} 条目 {it["bill_no"]} 超票面'})

        # 守恒3：在途付款金额合计 == 仍被在途占用的票据待付额。
        # 注意按“仍在途的付款轨迹”汇总，而非批次全额——批次可能已有部分条目退回。
        inflight_by_batch = {}
        for b in self.bills.values():
            for p in b.payments:
                if p['result'] == '在途':
                    inflight_by_batch[p['batch_no']] = inflight_by_batch.get(p['batch_no'], Decimal('0')) + D(p['amount'])
        inflight_total = add(*inflight_by_batch.values()) if inflight_by_batch else Decimal('0')
        occupied = add(*[D(b.outstanding) for b in self.bills.values() if b.open_batch_no]) \
            if any(b.open_batch_no for b in self.bills.values()) else Decimal('0')
        ok = inflight_total == occupied
        checks.append({'item': '在途资金与占用待付一致', 'ok': ok,
                       'expected': money(occupied), 'actual': money(inflight_total)})
        if not ok:
            blockers.append({'code': 'INFLIGHT_MISMATCH', 'detail': '在途批次金额与票据占用待付不一致'})

        # 守恒4：退回回报金额合计 == 所有“退回”轨迹金额合计
        report_total = add(*[r.amount for r in self.reports.values()]) if self.reports else Decimal('0')
        trace_return = add(*[p['amount'] for b in self.bills.values() for p in b.payments
                             if p['result'] == '退回']) if self.reports else Decimal('0')
        ok = report_total == trace_return
        checks.append({'item': '退回回报与退回轨迹一致', 'ok': ok,
                       'expected': money(trace_return), 'actual': money(report_total)})
        if not ok:
            blockers.append({'code': 'RETURN_TRACE_MISMATCH', 'detail': '退回回报金额与票据退回轨迹不符'})

        # 阻断：草稿恢复清单（含未续结回报）不能归档
        draft_recoveries = [r.recovery_no for r in self.recoveries.values() if r.status == '草稿']
        if draft_recoveries:
            blockers.append({'code': 'DRAFT_RECOVERY_OPEN',
                             'detail': f'存在未发出恢复清单: {",".join(draft_recoveries)}'})

        # 阻断：归档要求所有票据付清——仍有待付余额或在途付款都不能封存
        open_bills = [b.bill_no for b in self.bills.values() if b.outstanding > 0]
        inflight_bills = [b.bill_no for b in self.bills.values() if b.open_batch_no]
        if open_bills:
            blockers.append({'code': 'OUTSTANDING_NOT_ZERO',
                             'detail': f'仍有 {len(open_bills)} 张票据未付清: {",".join(open_bills[:6])}'})
        if inflight_bills:
            blockers.append({'code': 'INFLIGHT_OPEN',
                             'detail': f'仍有 {len(inflight_bills)} 张票据存在在途付款，待银行回报'})

        original = [b.to_dict() for b in self.batches.values() if b.kind == '原始批次']
        recovery = [b.to_dict() for b in self.batches.values() if b.kind == '恢复批次']
        totals = {
            '原始批次数': len(original),
            '恢复批次数': len(recovery),
            '原始批次面额': money(add(*[b['total'] for b in original]) if original else Decimal('0')),
            '恢复批次面额': money(add(*[b['total'] for b in recovery]) if recovery else Decimal('0')),
            '待付余额合计': money(add(*[b.outstanding for b in self.bills.values()])),
            '已付合计': money(add(*[b.paid_amount for b in self.bills.values()])),
            '应付合计': money(add(*[b.due_amount for b in self.bills.values()])),
        }
        balanced = not blockers
        return {'checks': checks, 'blockers': blockers, 'balanced': balanced,
                'totals': totals,
                'pay_identity_ok': D(totals['应付合计']) == D(totals['已付合计']) + D(totals['待付余额合计'])}

    # ---------- M7 归档封存（事务，失败留旧版） ----------
    def archive(self, note=''):
        rec = self.reconcile()
        if not rec['balanced'] or not rec['pay_identity_ok']:
            self.log('ARCHIVE_BLOCKED', {'blockers': [b['code'] for b in rec['blockers']]})
            raise BusinessError('ARCHIVE_BLOCKED',
                                '对账未平或存在阻断，已拒绝归档并保留原批次与旧版清单: '
                                + ';'.join(b['code'] for b in rec['blockers']), 409)
        snapshot = self.snapshot()
        seal = hashlib.sha256(
            json.dumps({'batches': sorted(self.batches.keys()),
                        'recoveries': sorted(self.recoveries.keys()),
                        'reports': sorted(self.reports.keys()),
                        'totals': rec['totals']}, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        version = len(self.archives) + 1
        record = {'version': version, 'seal': seal, 'note': note,
                  'totals': rec['totals'], 'revision': self.revision,
                  'snapshot': snapshot}
        self.archives.append(record)
        for batch in self.batches.values():
            if batch.frozen and batch.status != '已归档':
                batch.status = '已归档'
        self.log('ARCHIVE_OK', {'version': version, 'seal': seal[:12]})
        return {k: v for k, v in record.items() if k != 'snapshot'}

    # ---------- 指纹与快照 ----------
    def _seal(self, batch):
        payload = {'batch_no': batch.batch_no, 'kind': batch.kind,
                   'items': batch.items, 'total': batch.total}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def snapshot(self):
        return {
            'bills': {k: v.to_dict() for k, v in self.bills.items()},
            'dockets': self.dockets,
            'batches': {k: v.to_dict() for k, v in self.batches.items()},
            'reports': {k: v.to_dict() for k, v in self.reports.items()},
            'recoveries': {k: v.to_dict() for k, v in self.recoveries.items()},
            'archives': [{k: v for k, v in a.items() if k != 'snapshot'} for a in self.archives],
            'event_log': self.event_log,
            'seq': self.seq,
            'revision': self.revision,
        }
