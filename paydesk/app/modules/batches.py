from fastapi import APIRouter, Request, Body

from app.web import get_workbench, commit, render
from app.errors import require

router = APIRouter()


@router.get('/batches')
def page(request: Request):
    wb, _, _ = get_workbench()
    rows = [b.to_dict() for b in sorted(wb.batches.values(), key=lambda x: (x.kind, x.batch_no))]
    frozen = [b for b in rows if b['frozen']]
    view = {'title': '付款批次 · 冻结不可改写', 'rows': rows,
            'metrics': {'批次数': len(rows), '已冻结': len(frozen),
                        '原始批次': sum(1 for b in rows if b['kind'] == '原始批次'),
                        '恢复批次': sum(1 for b in rows if b['kind'] == '恢复批次')}}
    return render(request, 'batches', view)


@router.post('/api/batches')
def build_batch(payload: dict = Body(...)):
    require(payload.get('batch_no') and payload.get('docket_no') and payload.get('bill_nos'),
            'BAD_PAYLOAD', '需要 batch_no、docket_no、bill_nos')
    wb, repo, _ = get_workbench()
    batch = wb.build_batch(payload['batch_no'], payload['docket_no'], list(payload['bill_nos']))
    commit(wb, repo)
    return batch


@router.post('/api/batches/{batch_no}/issue')
def issue_batch(batch_no: str):
    wb, repo, _ = get_workbench()
    batch = wb.issue_batch(batch_no)
    commit(wb, repo)
    return batch


@router.post('/api/batches/{batch_no}/confirm')
def confirm_payment(batch_no: str, payload: dict = Body(...)):
    require(payload.get('bill_no'), 'BAD_PAYLOAD', '需要 bill_no')
    wb, repo, _ = get_workbench()
    bill = wb.confirm_payment(batch_no, payload['bill_no'])
    commit(wb, repo)
    return bill
