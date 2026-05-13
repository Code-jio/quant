import os

# Disable rate limiting during tests so that multiple test cases sharing the
# same module-level slowapi Limiter do not exhaust each other's quotas.
os.environ.setdefault("QUANT_RATE_LIMIT_ENABLED", "false")
