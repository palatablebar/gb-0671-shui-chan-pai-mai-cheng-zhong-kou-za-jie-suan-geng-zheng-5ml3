from fastapi import APIRouter, Request, Body

from app.web import get_workbench, commit, render
from app.errors import require

router = APIRouter()


@router.get('/returns')
def page(request: Request):
    wb, _, _ = get_workbench()
    rows = [r.to_dict() for r in sorted(wb.reports.values(), key=lambda x: x.report_no)]
    view = {'title': '银行退回回报 · 隔日 · 幂等', 'rows': rows,
            'metrics': {'回报数': len(rows),
                        '已续结': sum(1 for r in rows if r['recovered_by']),
                        '未续结': sum(1 for r in rows if not r['recovered_by'])}}
    return render(request, 'returns', view)


@router.post('/api/returns')
def register_return(payload: dict = Body(...)):
    keys = ('report_no', 'batch_no', 'bill_no', 'amount', 'bank_date', 'reason')
    require(all(k in payload for k in keys), 'BAD_PAYLOAD', f'需要字段: {",".join(keys)}')
    wb, repo, _ = get_workbench()

    # 第一道幂等：应用层回报号去重（重复执行直接回既有记录，不生成任何付款）
    if payload['report_no'] in wb.reports:
        hit = wb.register_return(payload['report_no'], payload['batch_no'], payload['bill_no'],
                                 payload['amount'], payload['bank_date'], payload['reason'])
        return hit

    result = wb.register_return(**{k: payload[k] for k in keys})
    commit(wb, repo)
    # 第二道幂等：DB 唯一约束兜底，防止并发重复回报
    stored = repo.remember_report(payload['report_no'], result['report'])
    if not stored:
        # 极小概率并发下已被另一请求登记：回读既有结果，保证不重复付款
        wb2, _, _ = get_workbench()
        return {'idempotent': True, 'report': wb2.reports[payload['report_no']].to_dict()}
    return result
