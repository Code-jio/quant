# CtpPlus SimNow 模拟盘环境配置指南

本指南介绍如何使用CtpPlus库连接到SimNow期货模拟交易环境。

## 安装CtpPlus

首先，安装CtpPlus库：

```bash
pip install ctpplus
```

## 配置SimNow账户

1. 访问SimNow官网注册账户：https://www.simnow.com.cn/
2. 获取模拟交易账号和密码
3. 记下您的投资者代码（通常以00开头）

## 配置文件设置

复制配置文件模板并填入您的SimNow账户信息：

```bash
cp config/config_ctp_plus_simnow.json config/config.json
```

编辑`config/config.json`文件，更新以下字段：

```json
{
    "trading": {
        "username": "YOUR_SIMNOW_ACCOUNT",      // 替换为您的SimNow账号
        "password": "YOUR_SIMNOW_PASSWORD",     // 替换为您的SimNow密码
        "broker_id": "9999",                   // SimNow模拟环境Broker ID
        "td_server": "tcp://180.168.146.187:10100",  // SimNow交易服务器
        "md_server": "tcp://180.168.146.187:10110",  // SimNow行情服务器
        "app_id": "simnow_client_test",        // SimNow测试AppID
        "auth_code": "0000000000000000"       // SimNow测试认证码
    }
}
```

## 运行测试

运行测试脚本来验证连接：

```bash
python test_ctp_plus_simnow.py
```

## 运行实盘交易（使用SimNow）

将配置文件设置为使用CtpPlus网关：

```json
{
    "mode": "live",
    "trading": {
        "gateway": "ctpplus",
        "account_type": "simnow",
        // ... 其他配置
    }
}
```

然后运行主程序：

```bash
python main.py
```

## 注意事项

1. **仅用于测试**：SimNow是模拟环境，仅用于学习和测试
2. **合约代码格式**：使用"交易所.合约代码"格式，如`"SHFE.rb2405"`
3. **交易时间**：请注意期货市场的交易时间，非交易时间可能无法连接
4. **安全密码**：不要在共享的代码库中暴露真实的账号密码
5. **风险控制**：即使是模拟环境，也要练习良好的风险控制习惯

## 常见问题

- 如果连接失败，请检查网络连接和防火墙设置
- 确保SimNow账号信息正确无误
- 检查是否为最新的CTP接口版本兼容

## 支持的交易所

- 上海期货交易所 (SHFE)
- 大连商品交易所 (DCE)
- 郑州商品交易所 (CZCE)
- 中国金融期货交易所 (CFFEX)
- 上海国际能源交易中心 (INE)

## 合约代码示例

- 沪深300指数期货: `CFFEX.IF2405`
- 螺纹钢期货: `SHFE.rb2405`
- 豆粕期货: `DCE.m2405`
- 甲醇期货: `CZCE.MA405`