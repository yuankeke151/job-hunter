# Job Hunter

基于 Python + Chrome CDP + Claude API + SQLite 的 BOSS直聘自动化求职工具。

## 环境依赖

```bash
pip install -r requirements.txt
```

## 使用方法

### 1. 职位扫描（job_scanner）

双击启动 Chrome A：

```
start_chrome_job.bat
```

在浏览器中手动登录 BOSS直聘，导航到目标职位搜索页，然后运行：

```bash
python src/scanner/scanner.py
```

脚本将自动扫描职位列表、AI 匹配分析、发起沟通。

---

### 2. 聊天处理（chat_handler）

双击启动 Chrome B：

```
start_chrome_chat.bat
```

在浏览器中手动登录 BOSS直聘，导航到 `/web/geek/chat`，然后运行：

```bash
python src/chat/handler.py
```

脚本将自动处理 IM 消息、点击同意卡片、发送简历、AI 回复。

---

## 主要配置项（src/config.py）

| 常量 | 说明 |
|------|------|
| `API_KEY` | AI API 密钥 |
| `SCORE_THRESHOLD` | AI 匹配分阈值，≥ 此分值才发起沟通（默认 70） |
| `MAX_GREET` | 单次运行最多打招呼数量（默认 10） |
| `CONTINUOUS_POLL` | `True` 持续轮询；`False` 处理一次后退出 |
| `REPLY_ENABLED` | `False` 时只做卡片同意和发简历，不发消息 |
| `SEND_ENABLED` | `False` 时消息打入输入框但不点击发送 |

## 注意事项

- 两个模块需分别启动独立的 Chrome 实例（端口 9222 / 9223）
- **不要以管理员身份运行** `.bat` 文件
- 每次重启 Chrome 后需重新手动登录
