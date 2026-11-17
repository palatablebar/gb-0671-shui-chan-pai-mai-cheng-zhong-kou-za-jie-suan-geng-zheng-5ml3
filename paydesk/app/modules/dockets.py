from decimal import Decimal

from fastapi import APIRouter, Request, Body

from app.web import get_workbench, commit, render
from app.errors import require

router = APIRouter()


@router.get('/dockets')
def page(request: Request):
    wb, _, _ = get_workbench()
    rows = [b.to_dict() for b in sorted(wb.bills.values(), key=lambda x: x.bill_no)]
    total = sum((Decimal(r['due_amount']) for r in rows), Decimal('0'))
    view = {'title': '日结清单 · 未付票', 'rows': rows,
            'metrics': {'票据数': len(rows), '应付合计': str(total)}}
    return render(request, 'dockets', view)


@router.post('/api/dockets')
def load_docket(payload: dict = Body(...)):
    require('docket_no' in payload and 'rows' in payload, 'BAD_PAYLOAD', '需要 docket_no 与 rows')
    wb, repo, _ = get_workbench()
    result = wb.load_docket(payload['docket_no'],
                            payload.get('settle_date', ''),
                            payload['rows'])
    commit(wb, repo)
    return result
