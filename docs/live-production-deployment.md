# 实盘生产部署（Docker）

Docker Compose 只提供内部 HTTP 上游：前端默认绑定 `127.0.0.1:8080`，后端不发布宿主机 `8000` 端口。对外访问必须由独立、受运维管理的 HTTPS 反向代理终止 TLS，并把请求转发到该本机前端端口；仓库内的 `front_end/nginx.conf` 不提供 TLS。

## 启动前置条件

在宿主机准备一份未纳入 Git 的实盘配置文件。文件必须符合服务端生产校验（实盘环境、完整风控、非空 `allowed_symbols`），并包含实际运行所需的 CTP 配置。配置路径和 CORS 源必须显式提供：

```bash
export QUANT_LIVE_CONFIG_HOST_PATH=/secure/path/quant-live-config.json
export QUANT_CORS_ORIGINS=https://trade.example.com
docker compose up -d --build
```

`QUANT_LIVE_CONFIG_HOST_PATH` 以只读方式挂载到容器 `/run/secrets/quant-live-config.json`。Compose 固定 `QUANT_ENV=production`、关闭合成行情、启用安全 Cookie 和限流；不能通过 Compose 环境变量放宽这些开关或使用 `100/1000` 这类宽松风控默认值。

Windows 启动前同样必须显式设置 `QUANT_CORS_ORIGINS` 为实际 HTTPS 前端源；`start.bat` 会强制生产、安全 Cookie、限流和禁用 WebSocket URL token，缺少 CORS 源时拒绝启动。

## 持久化范围

Compose 为以下运行状态创建独立持久卷：审计日志、会话库、实时风控状态以及 vn.py `.vntrader` 流目录。不要删除这些卷来“修复”问题；应先导出审计证据并按运维变更流程处理。

## 验证边界

Windows 的 `back_end/start.bat` 是当前已验证的实盘启动路径。Docker Compose 只通过静态配置校验，尚未在目标 Linux 发行版上完成 `vnpy_ctp`/CTP 原生 ABI、真实柜台连接、下单、撤单和断线恢复验证。部署到 Linux 前必须在目标镜像上完成该整套验收，且不得用模拟成交替代。

旧的 `python main.py --mode live` 入口已禁用，因为它不具备 API 路径的审计持久化、账户绑定会话和券商权威对账门禁。实盘必须从 `back_end/start.bat`（或完成同等现场验收后的受控容器入口）启动。
