"""Keep all test collection/imports independent of this machine's CTP secrets."""
import os
from pathlib import Path

for name in list(os.environ):
    if name.startswith("QUANT_CTP_"):
        del os.environ[name]
os.environ["QUANT_CTP_CONFIG"] = str(Path(__file__).parent / "fixtures/ctp.empty.json")
