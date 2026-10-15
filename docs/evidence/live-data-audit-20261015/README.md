# 验证材料

所有 PNG 都是 E2E 隔离样本，不能当作实盘资金、实盘行情或成交证据。实盘只读观察经最小化后记录在 live-read-only-checks.json，未保存账号和金额。

验证命令（backend 使用 Python 3.13 .venv-live，frontend 使用 Node 24）：

- `python -m ruff check main.py server.py src tests`：通过。
- `python -m mypy`：42 个文件通过。
- `python -m pytest -q -o addopts='' -p no:cacheprovider --basetemp <isolated-temp> --junitxml <output>`：260 项通过；JUnit 文件附在此目录。
- `npm run quality`：ESLint、vue-tsc、33 项单元测试和生产构建通过。
- `node ../scripts/check_bundle.mjs`：ECharts 734098/760000 字节；Element Plus 882069/910000 字节。
- 隔离 Playwright 配置：后端 18000 / 前端 55174，FakeGateway 拒绝真实发送；全量 24 项通过（54.7 秒），不使用实盘 8000/5174。

已有提示：FastAPI testclient 的 httpx 弃用警告；Vite 对大于 500 KB 的 vendor chunk 提示，仓库明确体积预算已通过。
