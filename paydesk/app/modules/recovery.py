from fastapi import APIRouter, Request, Body

from app.web import get_workbench, commit, render
from app.errors import require

router = APIRouter()


@router.get('/recovery')
def page(request: Request):
    wb, _, _ = get_workbench()
    rows = [r.to_dict() for r in sorted(wb.recoveries.values(), key=lambda x: x.recovery_no)]
    view = {'title': '恢复清单 · 退回款另成清单续付', 'rows': rows,
            'metrics': {'恢复清单': len(rows),
                        '草稿': sum(1 for r in rows if r['status'] == '草稿'),
                        '已发出': sum(1 for r in rows if r['status'] == '已发出')}}
    return render(request, 'recovery', view)


@router.post('/api/recovery')
def build_recovery(payload: dict = Body(...)):
    require(payload.get('recovery_no') and payload.get('report_nos'),
            'BAD_PAYLOAD', '需要 recovery_no 与 report_nos')
    wb, repo, _ = get_workbench()
    rec = wb.build_recovery(payload['recovery_no'], list(payload['report_nos']))
    commit(wb, repo)
    return rec


@router.post('/api/recovery/{recovery_no}/issue')
def issue_recovery(recovery_no: str, payload: dict = Body(...)):
    require(payload.get('batch_no'), 'BAD_PAYLOAD', '需要新批次号 batch_no（恢复批次独立编号）')
    wb, repo, _ = get_workbench()
    batch = wb.issue_recovery(recovery_no, payload['batch_no'])
    commit(wb, repo)
    return batch
