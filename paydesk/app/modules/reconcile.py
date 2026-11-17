from fastapi import APIRouter, Request

from app.web import get_workbench, render

router = APIRouter()


@router.get('/reconcile')
def page(request: Request):
    wb, _, _ = get_workbench()
    view = wb.reconcile()
    view['title'] = '批次对账 · 归档前须一致'
    return render(request, 'reconcile', view)


@router.get('/api/reconcile')
def api_reconcile():
    wb, _, _ = get_workbench()
    return wb.reconcile()
