from fastapi import APIRouter, Request, Body

from app import config
from app.web import get_workbench, commit, render
from app.repository import Repository

router = APIRouter()


@router.get('/archive')
def page(request: Request):
    wb, _, _ = get_workbench()
    rec = wb.reconcile()
    history = Repository(config.DB_PATH).archive_history()
    view = {'title': '归档封存 · 事务留旧版', 'reconcile': rec, 'history': history,
            'metrics': {'归档版本数': len(history),
                        '对账状态': '可归档' if rec['balanced'] and rec['pay_identity_ok'] else '阻断'}}
    return render(request, 'archive', view, extra={'rec': rec, 'history': history})


@router.post('/api/archive')
def archive(payload: dict = Body(default={})):
    wb, repo, _ = get_workbench()
    # service 内部先 reconcile；不平则抛 ARCHIVE_BLOCKED，不会进入 commit，旧版原样保留。
    record = wb.archive(note=payload.get('note', ''))
    # 同事务写快照 + 归档版本；失败整体回滚。
    commit(wb, repo, archived_version=record)
    return {'archived': record, 'history': repo.archive_history()}
