# CTP 期货交易网关安装和使用说明

## 已完成的工作

### 1. 安装了 TqSdk (天勤量化)

TqSdk 已成功安装，这是一个纯 Python 的期货交易库，支持：
- ✅ SimNow 免费模拟环境
- ✅ 快期实盘交易
- ✅ 无需编译，开箱即用

已安装的包：
- tqsdk 3.8.7
- tqsdk-ctpse 1.0.2 (CTP SE 接口)
- tqsdk-sm 1.0.5 (上期技术接口)

### 2. 创建的文件

| 文件 | 说明 |
|------|------|
| `src/trading/ctp_gateway.py` | CTP 网关框架（需要 CTP SDK） |
| `src/trading/tqsdk_gateway.py` | **TqSdk 网关（可用）** |
| `config/config_ctp_example.json` | CTP 配置示例 |
| `config/config_tqsdk_example.json` | TqSdk 配置示例 |

---

## 快速开始（使用 TqSdk 模拟环境）

### 1. 配置

将 `config/config_tqsdk_example.json` 复制到 `config/config.json`：

```bash
copy config\config_tqsdk_example.json config\config.json
```

### 2. 修改合约代码

编辑 `config/config.json`，修改 `symbol` 为你想要交易的合约：

```json
{
    "mode": "live",
    "strategy": {
        "name": "ma_cross",
        "symbol": "SHFE.rb2405",  // 螺纹钢 2025年5月合约
        "fast_period": 10,
        "slow_period": 20,
        "position_ratio": 0.8
    },
    "trading": {
        "gateway": "tqsdk",
        "account_type": "sim"
    }
}
```

### 3. 运行

```bash
python main.py
```

---

## 合约代码格式

TqSdk 使用交易所.合约格式，例如：

| 交易所 | 代码 | 合约格式 |
|--------|------|----------|
| 上海期货交易所 | SHFE | `SHFE.rb2405` (螺纹钢) |
| 大连商品交易所 | DCE | `DCE.m2405` (豆粕) |
| 郑州商品交易所 | CZCE | `CZCE.MA405` (甲醇) |
| 中国金融期货交易所 | CFFEX | `CFFEX.IH2405` (上证50) |
| 上海国际能源交易中心 | INE | `INE.sc2405` (原油) |

### 常见合约代码

| 合约 | 代码 | 交易所 |
|------|------|--------|
| 螺纹钢 | rb | SHFE |
| 铜 | cu | SHFE |
| 黄金 | au | SHFE |
| 豆粕 | m | DCE |
| 玉米 | c | DCE |
| 豆油 | y | DCE |
| 甲醇 | MA | CZCE |
| 棉花 | CF | CZCE |
| 白糖 | SR | CZCE |
| 沪深300 | IF | CFFEX |
| 中证500 | IC | CFFEX |
| 上证50 | IH | CFFEX |
| 原油 | sc | INE |

---

## TqSdk 网关功能状态

| 功能 | 状态 |
|------|------|
| 连接/登录 | ✅ 已实现 |
| 发送订单 | ✅ 已实现 |
| 撤销订单 | ✅ 已实现 |
| 查询账户 | ✅ 已实现 |
| 查询持仓 | ✅ 已实现 |
| 查询订单 | ✅ 已实现 |
| 订阅行情 | ✅ 已实现 |
| 实时行情 | ✅ 已实现 |

---

## 切换到快期实盘

如果你想使用真实账号交易：

1. 注册快期账号：https://www.kq.cc/

2. 修改 `config/config.json`：

```json
{
    "trading": {
        "gateway": "tqsdk",
        "account_type": "kq",
        "username": "你的快期账号",
        "password": "你的密码"
    }
}
```

---

## CTP 网关说明

`CTPGateway` 目前处于框架状态，需要：
1. 下载 CTP SDK（.dll 文件）
2. 安装 Microsoft Visual C++ Build Tools
3. 完善 TODO 部分代码

建议直接使用 TqSdk，它已经封装了 CTP 接口。

---

## 注意事项

1. **模拟环境**：SimNow 模拟环境是免费的，适合测试
2. **合约到期**：合约代码中的日期表示到期月份，如 `2405` 表示 2024年5月
3. **交易时间**：期货有夜盘，注意交易时间
4. **风险管理**：实盘交易前请充分测试
5. **保证金**：期货交易使用保证金，注意杠杆风险

---

## 获取帮助

- TqSdk 文档：https://doc.shinnytech.com/
- SimNow 官网：https://www.simnow.com.cn/
