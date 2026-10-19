"""Free EDB history via the documented HTTPS CSV API; no trading SDK/login.

Contract: https://doc.shinnytech.com/edb/latest/md_server.html
Only individual domestic futures are imported. Provider timestamps are retained
in Shanghai time; daily bars are already grouped by the provider's trading day.
"""
from __future__ import annotations

import csv
import hashlib
import io
from http.client import HTTPException as HttpTransportError
import re
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .governance import validate_bars
from .manager import DataManager
from ..watch.contracts import is_domestic_future

SOURCE = 'tianqin_edb'
URL = 'https://edb.shinnytech.com/md/kline'
MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 50000
COLUMNS = ['datetime_nano', 'open', 'high', 'low', 'close', 'volume', 'open_oi', 'close_oi']
EXCHANGES = ('SHFE', 'DCE', 'CZCE', 'CFFEX', 'INE', 'GFEX')


class EdbError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def today_shanghai() -> date:
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def capabilities(today: date | None = None) -> dict:
    today = today or today_shanghai()
    earliest = (pd.Timestamp(today) - pd.DateOffset(years=1)).date()
    return {'source': SOURCE, 'exchanges': EXCHANGES, 'timeframes': ['1d', '1m'],
            'minute_start_date': earliest.isoformat(), 'max_end_date': (today - timedelta(days=1)).isoformat(),
            'minute_max_days': 31, 'daily_max_days': 3660, 'requires_vendor_login': False,
            'documentation_url': 'https://doc.shinnytech.com/edb/latest/md_server.html'}


@dataclass(frozen=True)
class EdbQuery:
    exchange: str
    symbol: str
    timeframe: str
    start_date: date
    end_date: date

    @property
    def vendor_symbol(self) -> str:
        return f'{self.exchange}.{self.symbol}'

    def validated(self, today: date | None = None) -> EdbQuery:
        limits = capabilities(today)
        exchange, symbol = self.exchange.strip().upper(), self.symbol.strip()
        if '.' in symbol:
            prefix, symbol = symbol.split('.', 1)
            if prefix.upper() != exchange:
                raise EdbError('合约前缀与所选交易所不一致')
        symbol = symbol.upper() if exchange in ('CZCE', 'CFFEX') else symbol.lower()
        if not is_domestic_future({'exchange': exchange, 'symbol': symbol, 'product': 'FUTURES'}):
            raise EdbError('请选择国内期货月份合约，例如 SHFE.au2612；暂不导入主连、指数和期权')
        # Reject a known product paired with the wrong exchange without guessing a replacement.
        from ..watch import _ROOT_INDEX
        root = re.sub(r'\d+$', '', symbol).lower()
        product = _ROOT_INDEX.get(root)
        if product and product.get('exchange') != exchange:
            raise EdbError('合约品种与所选交易所不一致')
        if self.timeframe not in limits['timeframes']:
            raise EdbError('EDB 免费接口仅支持 1 分钟线和日线，其他周期不会自动补造')
        if self.end_date < self.start_date:
            raise EdbError('结束日期不能早于开始日期')
        if self.end_date.isoformat() > limits['max_end_date']:
            raise EdbError('历史导入截止到北京时间昨日，避免将尚未结束的 K 线用于回测')
        max_days = limits['minute_max_days'] if self.timeframe == '1m' else limits['daily_max_days']
        if (self.end_date - self.start_date).days + 1 > max_days:
            raise EdbError(f'单次最多下载 {max_days} 个自然日，请分段下载')
        if self.timeframe == '1m' and self.start_date.isoformat() < limits['minute_start_date']:
            raise EdbError('免费分钟线仅支持最近一年，请调整开始日期')
        return replace(self, exchange=exchange, symbol=symbol)


def _remote_error(status: int) -> EdbError:
    if status in (401, 403):
        return EdbError('EDB 拒绝访问：请检查免费分钟线的近一年范围及服务权限；没有导入数据', 502)
    if status == 429:
        return EdbError('EDB 请求过于频繁，请稍后重试', 503)
    return EdbError(f'EDB 服务返回错误（HTTP {status}），没有导入数据', 502)


def fetch_csv(query: EdbQuery) -> str:
    params = {'symbol': query.vendor_symbol, 'period': 60 if query.timeframe == '1m' else 86400,
              'start_time': f'{query.start_date} 00:00:00', 'end_time': f'{query.end_date} 23:59:59',
              'col': ','.join(COLUMNS[1:])}
    # EDB currently rejects an explicit text/csv Accept with HTTP 406. Keep the
    # generic Accept used by its requests example; validate CSV ourselves below.
    request = Request(URL + '?' + urlencode(params), headers={'Accept': '*/*', 'User-Agent': 'Quant-EDB/1.0'})
    started = time.monotonic()
    try:
        with urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise _remote_error(response.status)
            content = bytearray()
            expected = response.headers.get('Content-Length')
            if expected and (not str(expected).isdigit() or int(expected) > MAX_BYTES):
                raise EdbError('EDB 返回长度异常或数据过大，请缩短日期范围', 502)
            while chunk := response.read(65536):
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise EdbError('EDB 返回数据过大，请缩短日期范围', 502)
                if time.monotonic() - started > 40:
                    raise EdbError('EDB 下载超时，请缩短日期范围后重试', 504)
            if expected and len(content) != int(expected):
                raise EdbError('EDB 数据传输不完整，没有导入数据', 502)
            return content.decode('utf-8-sig')
    except HTTPError as exc:
        raise _remote_error(exc.code) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise EdbError('EDB 网络连接失败或超时，请稍后重试；没有导入数据', 504) from exc
    except HttpTransportError as exc:
        raise EdbError('EDB 数据传输中断，没有导入数据', 502) from exc
    except UnicodeError as exc:
        raise EdbError('EDB 响应不是有效的 UTF-8 CSV', 502) from exc


def parse_csv(text: str, query: EdbQuery) -> pd.DataFrame:
    """Reject malformed responses as a whole, before any database write."""
    first = text.lstrip('\ufeff').splitlines()[0] if text.strip() else ''
    if re.match(r'^\s*[45]\d\d,', first):
        raise _remote_error(int(first.strip()[:3]))
    try:
        reader = csv.reader(io.StringIO(text.lstrip('\ufeff')), strict=True)
        if next(reader, []) != COLUMNS:
            raise ValueError('unexpected columns')
        records: list[list[str]] = []
        for row in reader:
            if len(row) != len(COLUMNS) or len(records) >= MAX_ROWS:
                raise ValueError('invalid or excessive rows')
            records.append(row)
        if not records:
            return pd.DataFrame(columns=['datetime', 'open', 'high', 'low', 'close', 'volume', 'open_interest'])
        frame = pd.DataFrame(records, columns=COLUMNS)
        if not frame.datetime_nano.str.fullmatch(r'\d{1,19}').all():
            raise ValueError('invalid timestamp')
        # Integer conversion avoids losing nanoseconds through a floating-point intermediate.
        frame['datetime'] = pd.to_datetime(frame.datetime_nano.astype('int64'), unit='ns', utc=True).dt.tz_convert(
            'Asia/Shanghai').dt.tz_localize(None)
        for column in COLUMNS[1:]:
            frame[column] = pd.to_numeric(frame[column], errors='raise')
        if not np.isfinite(frame[COLUMNS[1:]].to_numpy(dtype=float)).all():
            raise ValueError('nonfinite values')
        for column in ('volume', 'open_oi', 'close_oi'):
            if ((frame[column] < 0) | (frame[column] % 1 != 0)).any():
                raise ValueError('invalid quantity')
        times = frame.datetime
        if ((times < pd.Timestamp(query.start_date)) | (times >= pd.Timestamp(query.end_date) + pd.Timedelta(days=1))).any():
            raise ValueError('out-of-range timestamp')
        if not times.eq(times.dt.floor('min' if query.timeframe == '1m' else 'D')).all():
            raise ValueError('unaligned timestamp')
        frame = frame.rename(columns={'close_oi': 'open_interest'}).drop(columns=['datetime_nano', 'open_oi'])
        validate_bars(frame)
        return frame.sort_values('datetime').reset_index(drop=True)
    except (ValueError, TypeError, OverflowError, csv.Error) as exc:
        raise EdbError('EDB 数据格式或质量校验失败，没有导入数据', 502) from exc


def import_history(query: EdbQuery, *, manager=None, downloader=None) -> dict:
    text = (downloader or fetch_csv)(query)
    frame = parse_csv(text, query)
    result = {'source': SOURCE, 'data_source': SOURCE, 'synthetic_data_used': False,
              'symbol': query.symbol, 'exchange': query.exchange, 'vendor_symbol': query.vendor_symbol,
              'timeframe': query.timeframe, 'start_date': str(query.start_date), 'end_date': str(query.end_date),
              'rows': len(frame), 'sha256': hashlib.sha256(text.encode()).hexdigest(),
              'timezone': 'Asia/Shanghai', 'first_datetime': None, 'last_datetime': None}
    if frame.empty:
        return result | {'status': 'empty', 'message': 'EDB 未返回该范围的数据；请检查合约、上市日期和服务更新情况'}
    dm = manager or DataManager()
    if not dm.save_bars(frame, query.symbol, query.timeframe, data_source=SOURCE):
        raise EdbError('历史数据保存失败，请检查磁盘和数据库状态', 500)
    from ..watch.kline import kline_cache
    kline_cache.invalidate(query.symbol)
    return result | {'status': 'imported', 'first_datetime': frame.datetime.iloc[0].isoformat(),
                     'last_datetime': frame.datetime.iloc[-1].isoformat(),
                     'message': '历史行情已保存；实际返回范围不保证覆盖请求内的全部交易时段'}
