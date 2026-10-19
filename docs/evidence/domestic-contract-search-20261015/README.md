# 国内期货搜索验收证据

日期：2026-10-15，北京时间。

- `search-fixture.png`：隔离 Playwright 测试截图，黄金名称带“筛选验收”；不是账户或市场行情证据。
- 后端：`ruff check main.py server.py src tests`、`mypy`、全量 `pytest`，288 passed。
- 前端：`npm run quality`，36 passed；`node ../scripts/check_bundle.mjs` 预算通过。
- 浏览器：独立 FakeGateway 18000 及 Vite 55174，完整 25 项通过。生产 8000 服务未用于自动报单测试。
- 真实柜台只读响应：AF 0 条；黄金 8 条（au2610、au2611、au2612、au2702、au2704、au2706、au2708、au2710）；CFFEX 24 条，产品类型均为 FUTURES，品种为 IC、IF、IH、T、TF、TL、TS。
- 用户同意重启并完成重新登录，已验证新服务响应。柜台环境是否属于真实资金生产环境仍未由本次搜索范围修复认证。
