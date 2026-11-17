import pytest
from fastapi.testclient import TestClient

from app import main
from app.state import seed_workbench


@pytest.fixture
def wb():
    return seed_workbench()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / 'paydesk.sqlite')
    # 只 patch config 模块属性；所有调用方运行时从 app.config 读取。
    monkeypatch.setattr('app.config.DB_PATH', path)
    return path


@pytest.fixture
def client(db_path):
    # 每个测试使用独立 tmp_path 数据库；lifespan 内首次播种。
    with TestClient(main.app) as c:
        yield c
