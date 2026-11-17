from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import unicodedata, re
from app.errors import BusinessError

_SENTINELS = {'', '-', 'N/A', 'NULL', '未知'}
_CENTS = Decimal('0.01')


def money(value):
    """金额统一两位小数、半分位四舍五入，返回字符串便于对账与 JSON 持久化。"""
    try:
        dec = Decimal(str(value))
        if not dec.is_finite():
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise BusinessError('BAD_MONEY', f'金额格式错误: {value!r}')
    return format(dec.quantize(_CENTS, rounding=ROUND_HALF_UP), 'f')


def parse_amount(value):
    text = unicodedata.normalize('NFKC', str(value)).strip()
    if text.upper() in _SENTINELS:
        raise BusinessError('MISSING_AMOUNT', '金额缺失')
    if ',' in text:
        if not re.fullmatch(r'[+-]?\d{1,3}(,\d{3})+(\.\d+)?', text):
            raise BusinessError('BAD_MONEY', '千分位格式错误')
        text = text.replace(',', '')
    return Decimal(money(text))


def D(value):
    return Decimal(str(value))


def add(*values):
    total = Decimal('0')
    for v in values:
        total += Decimal(str(v))
    return total


def cents(value):
    return Decimal(money(value))
