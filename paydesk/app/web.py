"""共享的工作台加载/保存与页面渲染辅助。"""
from fastapi import Request
from fastapi.templating import Jinja2Templates

from app import config
from app.config import ROOT, META
from app.repository import Repository
from app.state import _rebuild

templates = Jinja2Templates(directory=str(ROOT / 'app/templates'))


def get_workbench():
    repo = Repository(config.DB_PATH)
    snapshot = repo.load_raw()
    if snapshot is None:
        from app.state import seed_workbench
        return seed_workbench(), repo, True
    return _rebuild(snapshot), repo, False


def commit(wb, repo, archived_version=None):
    repo.save(wb.snapshot(), archived_version=archived_version)


def render(request: Request, key, view, extra=None):
    wb, repo, fresh = get_workbench()
    ctx = {'meta': META, 'key': key, 'wb': wb, 'view': view, 'fresh': fresh}
    if extra:
        ctx.update(extra)
    return templates.TemplateResponse(request=request, name=f'{key}.html', context=ctx)
