"""Run one executor, with loopback binding and no hot reload."""

import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.api:create_app",
        factory=True,
        host=os.getenv("QUANT_BIND_HOST", "127.0.0.1"),
        port=int(os.getenv("QUANT_PORT", "8000")),
        workers=1,
        reload=False,
        timeout_graceful_shutdown=10,
    )
