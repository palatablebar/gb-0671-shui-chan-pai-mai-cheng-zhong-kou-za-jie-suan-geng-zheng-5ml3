from pathlib import Path
import json, os

ROOT = Path(__file__).resolve().parents[1]
META = json.loads((ROOT / 'app/project.json').read_text())
MODULES = META['keys']
RULE_VERSION = '2026.11.17-paydesk-r1'
DB_PATH = os.environ.get('DB_PATH', str(ROOT / 'data' / 'paydesk.sqlite'))
MAX_BYTES = 2_000_000

# 批次一旦发出即进入冻结集合，禁止任何改写。
BATCH_FROZEN_STATUSES = ('已发出', '部分退回', '已归档')
# 票据生命周期
BILL_STATUSES = ('待付', '已发出', '部分退回', '已付清', '续付中', '续付发出')
# 退回原因码（银行隔日回报）
RETURN_REASONS = {
    'ACCOUNT_MISMATCH': '收款账号不符',
    'ACCOUNT_CLOSED': '收款账户已销户',
    'NAME_MISMATCH': '户名不一致',
    'BANK_REJECT': '银行拒付',
    'LIMIT_EXCEEDED': '超额退票',
}
