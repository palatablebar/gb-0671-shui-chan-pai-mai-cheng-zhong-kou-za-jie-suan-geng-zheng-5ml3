"""SQLite 持久化。

工作台整体序列化为一行 JSON；所有写操作在单个事务内提交，
归档命令失败时抛错回滚，磁盘上的旧批次与旧版清单原样保留。
另维护 idempotency 表，记录已处理银行回报号，作为应用层幂等的第二道防线。
"""
import json
import sqlite3
from pathlib import Path

from app.errors import BusinessError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workbench (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  payload TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS idempotency (
  key_type TEXT NOT NULL,
  key_value TEXT NOT NULL,
  result TEXT NOT NULL,
  PRIMARY KEY (key_type, key_value)
);
CREATE TABLE IF NOT EXISTS archive_versions (
  version INTEGER PRIMARY KEY,
  seal TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  payload TEXT NOT NULL
);
"""


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript(_SCHEMA)
    return con


class Repository:
    def __init__(self, path):
        self.path = path

    def load_raw(self):
        with connect(self.path) as con:
            row = con.execute('SELECT payload, revision FROM workbench WHERE id=1').fetchone()
        if not row:
            return None
        return json.loads(row['payload'])

    def save(self, snapshot, *, archived_version=None):
        """一次事务写工作台快照；archived_version 非空时同事务追加归档版本。

        任一步失败整体回滚，保证归档失败不会留下半截状态。
        """
        con = connect(self.path)
        try:
            con.execute('BEGIN IMMEDIATE')
            payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            con.execute(
                'INSERT INTO workbench(id,payload,revision,updated_at) VALUES(1,?,1,datetime(\'now\')) '
                'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, '
                'revision=workbench.revision+1, updated_at=datetime(\'now\')',
                (payload,))
            if archived_version is not None:
                con.execute(
                    'INSERT INTO archive_versions(version,seal,note,payload) VALUES(?,?,?,?)',
                    (archived_version['version'], archived_version['seal'],
                     archived_version.get('note', ''), payload))
            con.execute('COMMIT')
        except Exception as exc:
            con.execute('ROLLBACK')
            raise BusinessError('PERSIST_FAILED', f'持久化失败已回滚: {exc}', 500)
        finally:
            con.close()

    def archive_history(self):
        with connect(self.path) as con:
            rows = con.execute(
                'SELECT version,seal,note,created_at FROM archive_versions ORDER BY version').fetchall()
        return [dict(r) for r in rows]

    def remember_report(self, report_no, result):
        """回报号唯一约束兜底：重复插入即证明重复回报，返回 False。"""
        with connect(self.path) as con:
            try:
                con.execute('INSERT INTO idempotency(key_type,key_value,result) VALUES(?,?,?)',
                            ('bank_report', report_no, json.dumps(result, ensure_ascii=False)))
                return True
            except sqlite3.IntegrityError:
                return False
