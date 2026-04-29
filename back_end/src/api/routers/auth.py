"""认证路由：login / logout / status / servers。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ...trading import TradingEngine, TradingStatus
from .._constants import PRESET_MD, PRESET_TD
from ..deps import _account_to_dict, _record_audit
from ..schemas import (
    AuthStatusResponse,
    LoginRequest,
    LoginResponse,
)
from ..security import SESSION_COOKIE_MAX_AGE, SESSION_COOKIE_NAME, session_store
from ..state import trading_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.get("/servers", summary="获取预设服务器列表")
def get_servers():
    return {"td_servers": PRESET_TD, "md_servers": PRESET_MD}


@router.get(
    "/status",
    response_model=AuthStatusResponse,
    summary="当前连接状态（无需鉴权）",
)
def get_auth_status():
    engine = trading_state.primary_engine()
    connected    = False
    gw_status    = TradingStatus.STOPPED.value
    gw_name      = "N/A"
    account_id   = ""

    if engine:
        gw         = engine.gateway
        gw_name    = gw.name
        gw_status  = gw.status.value if isinstance(gw.status, TradingStatus) else str(gw.status)
        connected  = gw.status in (TradingStatus.CONNECTED, TradingStatus.TRADING)
        account_id = gw.account.account_id if hasattr(gw, "account") else ""

    return AuthStatusResponse(
        logged_in         = session_store.has_active_sessions(),
        gateway_connected = connected,
        gateway_status    = gw_status,
        gateway_name      = gw_name,
        account_id        = account_id,
        connect_log       = trading_state.get_log(),
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="CTP 账户登录",
)
async def do_login(body: LoginRequest, response: Response, request: Request):
    from ...trading import create_gateway  # noqa: PLC0415

    trading_state.clear_log()
    trading_state.add_log(f"开始连接账户: {body.username}")
    _record_audit(
        "auth", "login_attempt", "started",
        actor=body.username, request=request,
        detail={"gateway_type": body.gateway_type},
    )

    if trading_state._main_engine:
        trading_state.add_log("检测到已有连接，正在断开…")
        trading_state.clear_main()

    requested_gateway = (body.gateway_type or "vnpy").lower()
    if requested_gateway not in {"vnpy", "ctp"}:
        trading_state.add_log(f"不支持的网关类型: {body.gateway_type}")
        raise HTTPException(status_code=400, detail="仅支持 vn.py/CTP 网关")
    gateway_type = "vnpy"
    trading_state.add_log("使用 vn.py CTP 网关登录")

    config: Dict[str, Any] = {
        "gateway":    gateway_type,
        "username":   body.username,
        "password":   body.password,
        "broker_id":  body.broker_id,
        "td_server":  body.td_server,
        "md_server":  body.md_server,
        "app_id":     body.app_id,
        "auth_code":  body.auth_code,
        "vnpy_environment": body.environment,
        "connect_timeout": 25,
        "initial_capital": 0.0,
        "log_callback": trading_state.add_log,
    }

    trading_state.add_log(f"交易前置: {body.td_server}")
    trading_state.add_log(f"行情前置: {body.md_server}")

    gateway = None
    try:
        gateway = create_gateway(gateway_type)
        loop    = asyncio.get_running_loop()
        trading_state.add_log("正在连接，请稍候（最长 35 秒）…")

        success = await asyncio.wait_for(
            loop.run_in_executor(None, gateway.connect, config),
            timeout=35,
        )
    except asyncio.TimeoutError:
        if gateway:
            try:
                gateway.disconnect()
            except Exception:
                logger.exception("[API] 登录超时后断开网关异常")
        trading_state.add_log("✘ 连接超时（35s）")
        logger.error("[API] 登录超时（35s），请检查网络和服务器地址")
        raise HTTPException(status_code=408, detail="连接超时，请检查服务器地址或网络")
    except Exception as exc:
        if gateway:
            try:
                gateway.disconnect()
            except Exception:
                logger.exception("[API] 登录异常后断开网关异常")
        trading_state.add_log(f"✘ 连接异常: {exc}")
        raise HTTPException(status_code=500, detail=f"连接异常: {exc}")

    if not success:
        if gateway:
            try:
                gateway.disconnect()
            except Exception:
                logger.exception("[API] 登录失败后断开网关异常")
        error_summary = ""
        if gateway and hasattr(gateway, "connection_error_summary"):
            error_summary = gateway.connection_error_summary()
        if error_summary:
            detail = f"vn.py/CTP 连接失败：{error_summary}"
            trading_state.add_log(f"✘ {detail}")
            raise HTTPException(status_code=502, detail=detail)
        trading_state.add_log("✘ 登录失败，请检查账户信息")
        raise HTTPException(status_code=401, detail="登录失败，请检查账户/密码/经纪商ID")

    engine = TradingEngine(gateway)

    try:
        account    = gateway.query_account()
        account_id = account.account_id if account else ""
        balance    = account.balance    if account else 0.0
    except Exception:
        account_id, balance = "", 0.0

    if balance > 0:
        config["initial_capital"] = balance
    engine.configure_risk(config)
    trading_state.set_main_engine(engine, config)
    trading_state._day_open_balance = balance
    engine.risk_manager.set_day_open_balance(balance)

    # ── 自动启动策略 ──────────────────────────────────────────────────────
    strategy_started = False
    strategy_id = ""
    if body.auto_start_strategy:
        from ...strategy import create_strategy  # noqa: PLC0415
        from ..deps import _subscribe_market_ticks  # noqa: PLC0415

        strategy_params = dict(body.strategy_params or {})
        if not strategy_params.get("symbol") and not strategy_params.get("symbols"):
            raise HTTPException(status_code=400, detail="自动启动策略需要 strategy_params.symbol 或 symbols")
        strategy = create_strategy(body.strategy_name, strategy_params)
        strategy.initial_capital = config.get("initial_capital", 0.0) or balance or 1_000_000.0
        engine.set_strategy(strategy)

        strategy_config = {
            **config,
            "strategy_name": body.strategy_name,
            "strategy_params": strategy_params,
        }
        if not engine.start(strategy_config):
            raise HTTPException(status_code=500, detail="策略运行时启动失败")

        strategy_id = f"{body.strategy_name}_main"
        trading_state.register(strategy_id, strategy, engine, strategy_config)
        symbols = strategy_params.get("symbols") or [strategy_params.get("symbol")]
        _subscribe_market_ticks(engine, [s for s in symbols if s])
        strategy_started = True
        trading_state.add_log(f"策略已自动启动: {strategy_id}")

    trading_state.add_log(f"✔ 登录成功，账户: {account_id}，当前余额: ¥{balance:,.2f}（用作初始资金基准）")

    # Cookie secure: 非 localhost 时启用
    secure = request.url.hostname not in ("localhost", "127.0.0.1")
    token = session_store.create()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
    )

    _record_audit(
        "auth", "login", "success",
        actor=body.username, request=request,
        detail={"gateway_type": gateway_type, "account_id": account_id},
    )

    return LoginResponse(
        success        = True,
        token          = token,
        message        = "登录成功",
        gateway_status = gateway.status.value,
        account_id     = account_id,
        balance        = balance,
        strategy_started = strategy_started,
        strategy_id      = strategy_id,
    )


@router.post("/logout", summary="断开连接并注销会话")
async def do_logout(request: Request):
    auth  = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if token:
        session_store.revoke(token)
    request_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if request_token:
        session_store.revoke(request_token)
    resp = JSONResponse({"success": True, "message": "已断开连接"})
    resp.delete_cookie(SESSION_COOKIE_NAME)
    trading_state.add_log("用户主动断开连接")
    _record_audit("auth", "logout", "success", request=request)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, trading_state.clear_main)
    return resp
