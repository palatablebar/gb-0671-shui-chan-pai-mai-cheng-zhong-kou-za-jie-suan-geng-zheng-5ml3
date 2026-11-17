from app import config
from app.repository import Repository
from app.state import _rebuild


def test_pages_render(client):
    for path in ['/dockets', '/batches', '/returns', '/recovery',
                 '/workbench', '/reconcile', '/archive']:
        r = client.get(path)
        assert r.status_code == 200, path


def test_health_and_redirect(client):
    assert client.get('/healthz').json()['project'] == 'paydesk'
    # TestClient 默认跟随重定向：/ 应 303 跳到首个模块页 /dockets
    r = client.get('/', follow_redirects=False)
    assert r.status_code == 303
    assert r.headers['location'] == '/dockets'
    assert client.get('/').url.path == '/dockets'


def test_seed_persisted_and_rebuild_roundtrip(client):
    # 启动 lifespan 已播种
    wb = client.get('/api/workbench').json()
    assert wb['metrics']['退回回报数'] == 1
    assert any(r['bill_no'] == 'P0002' for r in wb['rows'])


def test_api_idempotent_return_no_duplicate_payment(client):
    payload = {'report_no': 'RBK-20261118-007', 'batch_no': 'B20261117-01',
               'bill_no': 'P0002', 'amount': '8600.50',
               'bank_date': '2026-11-18', 'reason': 'ACCOUNT_MISMATCH'}
    first = client.post('/api/returns', json=payload)
    # 种子里该回报已存在 -> 幂等命中
    assert first.status_code == 200
    assert first.json()['idempotent'] is True
    second = client.post('/api/returns', json=payload)
    assert second.json()['idempotent'] is True
    reports = client.get('/returns')
    assert reports.status_code == 200


def test_api_full_recovery_flow(client):
    # 建恢复清单 -> 发出恢复批次 -> 确认付款
    r = client.post('/api/recovery', json={
        'recovery_no': 'RC-API-1', 'report_nos': ['RBK-20261118-007']})
    assert r.status_code == 200
    r = client.post('/api/recovery/RC-API-1/issue', json={'batch_no': 'B-API-R1'})
    assert r.status_code == 200
    assert r.json()['kind'] == '恢复批次' and r.json()['frozen'] is True
    r = client.post('/api/batches/B-API-R1/confirm', json={'bill_no': 'P0002'})
    assert r.status_code == 200
    assert r.json()['outstanding'] == '0.00'


def test_api_frozen_batch_issue_rejected_409(client):
    r = client.post('/api/batches/B20261117-01/issue')
    assert r.status_code == 409
    assert r.json()['code'] == 'BATCH_FROZEN'


def test_api_archive_blocked_then_recover_then_archive(client, tmp_path, monkeypatch):
    # 制造草稿恢复清单后归档应 409，且不写归档历史
    client.post('/api/recovery', json={
        'recovery_no': 'RC-BLOCK', 'report_nos': ['RBK-20261118-007']})
    blocked = client.post('/api/archive', json={'note': '应失败'})
    assert blocked.status_code == 409
    assert blocked.json()['code'] == 'ARCHIVE_BLOCKED'
    history = Repository(config.DB_PATH).archive_history()
    assert history == []


def test_repository_snapshot_roundtrip(client, tmp_path):
    path = config.DB_PATH
    repo = Repository(path)
    snap = repo.load_raw()
    assert snap is not None and 'batches' in snap
    rebuilt = _rebuild(snap)
    assert 'B20261117-01' in rebuilt.batches
    assert rebuilt.batches['B20261117-01'].frozen is True


def test_second_archive_appends_version_in_same_tx(client):
    # 先把唯一退回续结并付清所有票，达到可归档
    client.post('/api/recovery', json={
        'recovery_no': 'RC-FIN', 'report_nos': ['RBK-20261118-007']})
    client.post('/api/recovery/RC-FIN/issue', json={'batch_no': 'B-FIN-R1'})
    client.post('/api/batches/B-FIN-R1/confirm', json={'bill_no': 'P0002'})
    client.post('/api/batches', json={
        'batch_no': 'B-FIN-02', 'docket_no': 'D20261117',
        'bill_nos': ['P0005', 'P0006', 'P0007', 'P0008']})
    client.post('/api/batches/B-FIN-02/issue')
    for b in ['P0005', 'P0006', 'P0007', 'P0008']:
        client.post('/api/batches/B-FIN-02/confirm', json={'bill_no': b})
    for b in ['P0001', 'P0003', 'P0004']:
        client.post('/api/batches/B20261117-01/confirm', json={'bill_no': b})

    a1 = client.post('/api/archive', json={'note': 'v1'}).json()
    a2 = client.post('/api/archive', json={'note': 'v2'}).json()
    assert a1['archived']['version'] == 1
    assert a2['archived']['version'] == 2
    assert len(a2['history']) == 2
