"""领域模型：票据、付款批次、退回回报、恢复清单、付款轨迹。

设计约束（对应业务规则）：
- 付款批次一旦“已发出”即冻结（frozen=True），任何条目/金额改写都被服务层拒绝；
- 票据金额以两位小数字符串持久化，待付余额 = 应付 - 成功付款，恒非负；
- 退回不冲减改写原批次，只在票据轨迹上登记一笔“退回”，再由恢复清单续付。
"""
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from app.money_utils import money, D


@dataclass
class PaymentEntry:
    """票据上的一条付款轨迹（在途/成功/退回）。"""
    batch_no: str
    kind: str               # 原始批次 / 恢复批次
    amount: str
    result: str = '在途'     # 在途 / 成功 / 退回
    report_no: str = ''     # 若被退回，登记银行回报号
    seq: int = 0

    def to_dict(self):
        return asdict(self)


@dataclass
class Bill:
    bill_no: str
    vendor: str
    docket_no: str
    settle_date: str
    due_amount: str
    paid_amount: str = '0.00'
    payments: list = field(default_factory=list)   # list[PaymentEntry dict]
    open_batch_no: str = ''                        # 当前占用待付余额的在途批次

    @property
    def outstanding(self) -> Decimal:
        return D(self.due_amount) - D(self.paid_amount)

    @property
    def has_return(self) -> bool:
        return any(p['result'] == '退回' for p in self.payments)

    @property
    def last_payment(self):
        return self.payments[-1] if self.payments else None

    def derive_status(self) -> str:
        if self.outstanding <= 0:
            return '已付清'
        # 在途占用：有一笔在途付款（原始或恢复）
        inflight = [p for p in self.payments if p['result'] == '在途']
        if inflight:
            kind = inflight[-1]['kind']
            return '续付发出' if kind == '恢复批次' else '已发出'
        if self.has_return:
            return '续付中' if self.open_batch_no else '部分退回'
        return '待付'

    def to_dict(self):
        return {
            'bill_no': self.bill_no,
            'vendor': self.vendor,
            'docket_no': self.docket_no,
            'settle_date': self.settle_date,
            'due_amount': self.due_amount,
            'paid_amount': self.paid_amount,
            'outstanding': money(self.outstanding),
            'status': self.derive_status(),
            'open_batch_no': self.open_batch_no,
            'payments': self.payments,
        }


@dataclass
class BatchItem:
    bill_no: str
    vendor: str
    amount: str

    def to_dict(self):
        return asdict(self)


@dataclass
class PaymentBatch:
    batch_no: str
    kind: str                  # 原始批次 / 恢复批次
    docket_no: str
    status: str = '待发出'      # 待发出 / 已发出 / 部分退回 / 已结清 / 已归档
    frozen: bool = False
    items: list = field(default_factory=list)    # list[BatchItem dict]
    total: str = '0.00'
    source_batches: list = field(default_factory=list)   # 恢复批次：来源原始批次号
    source_reports: list = field(default_factory=list)   # 恢复批次：来源回报号
    recovery_of: str = ''       # 恢复清单号（草稿转批次时回填）
    seq: int = 0
    seal: str = ''

    def to_dict(self):
        return asdict(self)


@dataclass
class ReturnReport:
    report_no: str             # 银行回报号，幂等键（唯一）
    batch_no: str
    bill_no: str
    amount: str
    bank_date: str
    reason: str
    recovered_by: str = ''     # 已被哪个恢复批次续结
    seq: int = 0

    def to_dict(self):
        return asdict(self)


@dataclass
class RecoveryItem:
    report_no: str
    source_batch_no: str
    bill_no: str
    vendor: str
    amount: str

    def to_dict(self):
        return asdict(self)


@dataclass
class RecoveryList:
    """退回款恢复清单（草稿）。发出后转成一个冻结的“恢复批次”。"""
    recovery_no: str
    docket_no: str
    status: str = '草稿'        # 草稿 / 已发出
    items: list = field(default_factory=list)   # list[RecoveryItem dict]
    total: str = '0.00'
    issued_batch_no: str = ''
    seq: int = 0
    src_batches: list = field(default_factory=list)   # 来源原始批次号（持久化）
    src_reports: list = field(default_factory=list)   # 来源回报号（持久化）

    def to_dict(self):
        return asdict(self)
