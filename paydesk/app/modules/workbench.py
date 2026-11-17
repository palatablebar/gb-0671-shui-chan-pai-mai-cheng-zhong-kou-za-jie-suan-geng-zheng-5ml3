from fastapi import APIRouter, Request

from app.web import get_workbench, render

router = APIRouter()


@router.get('/workbench')
def page(request: Request):
    wb, _, _ = get_workbench()
    view = wb.workbench_view()
    view['title'] = '续结工作台 · 已续结 / 仍未付清'
    return render(request, 'workbench', view)


@router.get('/api/workbench')
def api_view():
    wb, _, _ = get_workbench()
    return wb.workbench_view()
