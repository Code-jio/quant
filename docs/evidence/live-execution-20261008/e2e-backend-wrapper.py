"""Run the existing offline fixture on an isolated port, preserving live :8000."""
import runpy
import uvicorn

original_run = uvicorn.run


def run_on_fixture_port(*args, **kwargs):
    kwargs["port"] = 18000
    return original_run(*args, **kwargs)


uvicorn.run = run_on_fixture_port
runpy.run_path("D:/mine/quant/back_end/tests/e2e_backend.py", run_name="__main__")
