"""仪表盘路由：metrics。"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import _build_dashboard_metrics

router = APIRouter(tags=["仪表盘"])


@router.get("/dashboard/metrics", summary="全局仪表盘指标快照")
def get_dashboard_metrics():
    return _build_dashboard_metrics()
