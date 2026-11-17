from contextlib import asynccontextmanager
from importlib import import_module

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.config import ROOT, META, MODULES
from app.errors import BusinessError
from app.repository import Repository
from app.state import seed_workbench


def bootstrap():
    """首次启动用样例播种；已有持久化则保持不变。"""
    repo = Repository(config.DB_PATH)
    if repo.load_raw() is None:
        wb = seed_workbench()
        repo.save(wb.snapshot())


@asynccontextmanager
async def lifespan(app):
    bootstrap()
    yield


app = FastAPI(title=META['title'], lifespan=lifespan)
app.mount('/static', StaticFiles(directory=str(ROOT / 'app/static')), name='static')


@app.exception_handler(BusinessError)
async def business_error(request: Request, error: BusinessError):
    return JSONResponse({'code': error.code, 'detail': error.message}, status_code=error.status)


@app.get('/')
def home():
    return RedirectResponse('/' + MODULES[0], status_code=303)


@app.get('/healthz')
def health():
    return {'status': 'ok', 'project': META['short']}


for key in MODULES:
    app.include_router(import_module(f'app.modules.{key}').router)
