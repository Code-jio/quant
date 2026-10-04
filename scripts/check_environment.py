"""Check the documented offline runtime without loading a native broker."""
import importlib.metadata
import platform
import sys

print('Python', platform.python_version(), platform.machine())
if sys.version_info[:2] != (3, 12):
    raise SystemExit('The verified backend environment requires Python 3.12.')
for name in ('fastapi', 'pandas', 'numpy', 'uvicorn', 'websockets'):
    print(name, importlib.metadata.version(name))
print('Offline runtime available. Native CTP and broker acceptance are separate checks.')
