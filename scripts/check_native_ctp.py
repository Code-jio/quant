"""Verify native CTP loading and optional front handshakes, without account login.

Run with back_end/.venv-live/Scripts/python. --fronts contacts only the configured
TD/MD fronts; it never authenticates, logs into an account, subscribes or orders.
Each native API runs in a bounded child process to contain DLL failures.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "back_end"))
MARKER = "QUANT_NATIVE_RESULT="


def child_probe(kind: str) -> None:
    from src.settings import ctp_defaults
    from src.trading.ctp_observer import install_observer
    from vnpy.event import EventEngine
    from vnpy_ctp import CtpGateway
    from vnpy_ctp.api import MdApi, TdApi

    if kind == "runtime":
        gateway = CtpGateway(EventEngine(), "NATIVE_CHECK")
        install_observer(gateway, lambda _event: None)
        result = {
            "native_imports": True,
            "observer_installed": True,
            "production_environment_supported": "实盘" in gateway.default_setting.get("柜台环境", []),
        }
    else:
        settings = ctp_defaults()
        address = settings[f"{kind}_server"]
        if not address:
            raise ValueError("Configured front address is required")
        if settings["vnpy_environment"] != "实盘":
            raise ValueError("This probe requires the configured live API environment")

        class FrontProbe(MdApi if kind == "md" else TdApi):
            def __init__(self):
                super().__init__()
                self.event = threading.Event()
                self.connected = False
                self.disconnect_reason = None

            def onFrontConnected(self):
                self.connected = True
                self.event.set()

            def onFrontDisconnected(self, reason):
                self.disconnect_reason = reason
                self.event.set()

        probe = FrontProbe()
        prefix = str(Path.cwd() / kind).encode("GBK")
        if kind == "md":
            probe.createFtdcMdApi(prefix, True)
        else:
            probe.createFtdcTraderApi(prefix, True)
        try:
            probe.registerFront(address)
            probe.init()
            probe.event.wait(12)
            result = {
                "kind": kind, "front_connected": probe.connected,
                "disconnect_reason": probe.disconnect_reason, "production_mode": True,
                "account_login_attempted": False,
            }
        finally:
            probe.exit()
    print(MARKER + json.dumps(result), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fronts", action="store_true", help="Connect to saved live TD/MD fronts without logging in")
    parser.add_argument("--child", choices=("runtime", "td", "md"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child_probe(args.child)
        return 0

    results = {"python": platform.python_version(), "architecture": platform.machine()}
    try:
        results["packages"] = {name: importlib.metadata.version(name) for name in ("vnpy", "vnpy_ctp")}
    except importlib.metadata.PackageNotFoundError:
        print(json.dumps({**results, "error": "Install requirements-live.lock in the live environment first"}))
        return 1
    child_env = {**os.environ, "PYTHONUTF8": "1"}
    if child_env.get("QUANT_CTP_CONFIG"):
        child_env["QUANT_CTP_CONFIG"] = str(Path(child_env["QUANT_CTP_CONFIG"]).resolve())
    passed = True
    for kind in ("runtime", "td", "md") if args.fronts else ("runtime",):
        # Isolate vn.py settings, log and CTP flow paths before importing the SDK.
        with tempfile.TemporaryDirectory(prefix="quant-native-") as folder:
            Path(folder, ".vntrader").mkdir()
            try:
                process = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--child", kind],
                    cwd=folder, env=child_env, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=20, check=False,
                )
                payload = next((line[len(MARKER):] for line in process.stdout.splitlines() if line.startswith(MARKER)), "")
                if process.returncode or not payload:
                    raise RuntimeError(f"Native child exited with code {process.returncode}")
                data = json.loads(payload)
                passed = passed and (all(data.values()) if kind == "runtime" else data["front_connected"])
            except (subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
                data = {"error": str(exc)}
                passed = False
            results[kind] = data
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
