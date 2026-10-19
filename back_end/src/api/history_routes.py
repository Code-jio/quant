"""Public market history ingestion, isolated from broker execution and account locks."""
from datetime import date
from threading import Lock
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..data.edb import EdbError, EdbQuery, capabilities, import_history


class HistoryImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    exchange: Literal['SHFE', 'DCE', 'CZCE', 'CFFEX', 'INE', 'GFEX']
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: Literal['1d', '1m'] = '1d'
    start_date: date
    end_date: date


def register_history_routes(app, record_audit):
    download_lock = Lock()

    @app.get('/data/history/edb', tags=['历史行情'])
    def edb_capabilities():
        return capabilities()

    @app.post('/data/history/edb/import', tags=['历史行情'])
    def download_history(body: HistoryImportRequest, request: Request):
        try:
            query = EdbQuery(**body.model_dump()).validated()
        except EdbError as exc:
            raise HTTPException(exc.status_code, str(exc)) from None
        if not download_lock.acquire(blocking=False):
            raise HTTPException(409, '已有历史数据下载正在进行，请等待完成后重试')
        try:
            result = import_history(query)
            record_audit('history', 'edb_import', result['status'], request=request, resource=query.vendor_symbol, detail=result)
            return result
        except EdbError as exc:
            raise HTTPException(exc.status_code, str(exc)) from None
        finally:
            download_lock.release()
