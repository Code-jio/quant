# EDB 接入验证记录（2026-10-19）

以下检查在本轮代码修改完成后执行。实际外部下载与固定样本浏览器测试分别记录，不能混作实盘运行证据。

| 检查 | 结果 | 证据 |
|---|---|---|
| 后端 Ruff | 通过 | [ruff.txt](ruff.txt) |
| 后端 mypy | 52 个源文件通过 | [mypy.txt](mypy.txt) |
| 后端完整 pytest | 350 通过；1 条现有 Starlette 弃用警告 | [backend.txt](backend.txt) |
| 前端 quality | ESLint、类型检查、38 个测试、生产构建通过 | [frontend.txt](frontend.txt) |
| 前端体积预算 | ECharts、Element Plus 均在现有预算内 | [bundle.txt](bundle.txt) |
| Playwright 完整 E2E | 33 通过，含 5 个 EDB 场景 | [browser.txt](browser.txt) |
| 官方 EDB 实际 HTTPS | 黄金 5 根日线、555 根分钟线解析并保存至临时隔离库；来源均为 tianqin_edb | [live-http.json](live-http.json) |

后端完整检查使用 `.venv-live`，浏览器测试使用项目原有 Playwright 配置及隔离服务。所有交易相关测试使用 FakeGateway，没有连接柜台或提交报撤单。真实 EDB 抽查的临时数据库在验证后清理，没有写入用户实盘数据库。

浏览器截图均为 **CSV 固定测试样本**，图中价格不是官方实时行情，也不是实际 HTTPS 抽查数据：

- [日线导入结果](edb-daily-import.png)：日期、实际返回范围和来源回执。
- [回测页面](edb-backtest-desktop.png)：导入结果填入回测参数并展示来源。
- [分钟线图表](edb-minute-chart.png)：导入后图表读取本地数据库。
- [手机尺寸弹窗](edb-mobile.png)：反馈、表单与操作按钮可达。

EDB 专项测试还覆盖错误响应、空结果、无效数据、重复导入、未登录、并发下载、下载不阻塞账户状态与注销，以及旧后端未加载接口的提示。测试不会用模拟行情替代下载失败。

**部署边界：** 本地 8000 / 5174 没有监听，启动命令两次被自动审批以 `blocked by policy` 拒绝。尚未验证启动后的实盘页面加载；启动方法见[接入报告](../../EDB_HISTORY_2026-10-19.md)。
