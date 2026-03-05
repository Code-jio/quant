# SimNow仿真交易使用说明

## 配置文件
- `config/config_simnow.json` - SimNow仿真交易配置文件
- 将您的SimNow账户信息填入此文件

## 使用方法
1. 编辑 `config/config_simnow.json` 文件，将以下字段替换为您的真实信息：
   - `username`: 您的SimNow用户名
   - `password`: 您的SimNow密码

2. 运行仿真交易：
   ```bash
   python main.py
   ```

   或者在代码中指定配置：
   ```python
   # 在代码中加载SimNow配置
   config_path = "config/config_simnow.json"
   ```

## 注意事项
- 仿真交易环境与真实交易略有差异
- 请确保网络连接稳定
- 注意账户资金和风险管理
- 仿真交易系统可能会有延迟