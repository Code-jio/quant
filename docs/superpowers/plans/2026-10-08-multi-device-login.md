# 同账户多端登录 Implementation Plan

> **Execution note:** 本次由用户授权直接实现，在当前会话内执行与验证，保留运行中的实盘后端，不做真实登录、下单或撤单。

**Goal:** 同一交易账户在多个独立浏览器中登录，共用一个 CTP 引擎；退出当前端不影响其他端。

**Architecture:** 首次柜台登录成功后，仅在内存保存随机盐和 PBKDF2-HMAC-SHA256 密码校验值，绑定账号、经纪商、前置、环境及应用配置。后续同配置登录验证密码并发放独立 HttpOnly Cookie，不新建柜台连接、不重置风险或账本。保留既有 `/auth/logout` 的全局断开语义以兼容旧客户端，新增 `/auth/session/logout` 注销当前 Cookie；明确区分两个 UI 操作。

**Tech Stack:** FastAPI、Python hashlib/secrets、Vue/Pinia、Vitest、Playwright。

## 执行与验证

- [x] 在 `back_end/tests/test_multi_device_auth.py` 添加隔离回归：同账户独立 Cookie、错误密码拒绝、配置/账户隔离、退出后其他端继续访问、全局断开、Cookie 轮换及会话过期、限流、账本与引擎对象保持不变。先运行新增测试确认缺失行为。
- [x] 在 `back_end/src/api/security.py` 实现只存于内存的凭证校验与有限大小的失败限流；每地址 60 秒内 5 次认证失败后返回 429。校验参数使用 32 字节随机盐、600000 轮 PBKDF2-HMAC-SHA256、constant-time 比较；不输出密码、校验值或会话令牌。为登录字段设置长度限制。
- [x] 在 `back_end/src/api/__init__.py` 接入共享连接登录、32 个会话上限和单端注销；首次连接/切换失败保持旧状态。已授权的显式连接/风险配置变更继续按原有全账户切换语义处理，匿名第二端不得更改运行参数。登录/状态响应明确是否复用连接及当前会话数。
- [x] 在 `front_end/src/stores/auth.js`、`router/index.js` 恢复有效 Cookie 的登录状态；新标签页不需再次连接柜台。`DashboardView.vue` 增加“退出当前端”和带影响范围确认的“断开全部”；接口失败保留本地状态，避免旧后端误触发全局退出。`LoginView.vue` 说明同账户共享连接。
- [x] 在 `front_end/tests/e2e/multi-device.spec.js` 用两个隔离 BrowserContext 登录同一 FakeGateway，验证不同 Cookie、刷新/新标签恢复、单端退出隔离、重新加入、全局断开及错误密码。不复制 Cookie、不使用真实账号。
- [x] 执行后端完整 pytest/Ruff/mypy、前端 quality、独立 18000/55174 端口 E2E 和包体预算；更新 README、问题清单及本功能验证记录，检查 Git 差异及凭证泄露后提交。

## 运行边界

校验值只对当前柜台连接有效，重启/全局断开/切换时清除。它证明密码与本次成功柜台登录一致，不重新请求柜台验证密码；外部改密后需明确断开并重新登录才能刷新校验值。各端共享账户风控、订单、急停及策略权限，不实现多账户并行执行、分级角色或单设备管理页面。

当前服务仍运行旧代码，验证仅使用隔离服务。上线需要用户愿意结束当前会话后重启并重新登录。跨设备访问需访问同一后端的可达地址，`127.0.0.1` 只代表各设备自身；不自动开放防火墙或公网。

参考：[Python hashlib](https://docs.python.org/3/library/hashlib.html#hashlib.pbkdf2_hmac)、[OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)。
