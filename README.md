# SJTU Agent

[![Test](https://github.com/kuan-er/sjtu-agent/actions/workflows/test.yml/badge.svg)](https://github.com/kuan-er/sjtu-agent/actions/workflows/test.yml)

面向上海交通大学学生的校园助手，提供终端对话、飞书 / Telegram / 微信 / QQ Bot、DDL 聚合、日报推送和 MCP Server。

[English Version](README_EN.md) · [项目展示页](https://kuan-er.github.io/sjtu-agent)

如果这个项目对你有帮助，欢迎点一个 ⭐ Star！

---

## 快速开始

```bash
# macOS / Linux
git clone https://github.com/kuan-er/sjtu-agent.git && cd sjtu-agent && bash install.sh

# Windows PowerShell
git clone https://github.com/kuan-er/sjtu-agent.git; cd sjtu-agent; powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装脚本自动创建 `.venv`、安装依赖和 Playwright Chromium，然后启动 `sjtu-agent setup`。setup 向导引导你配置大模型 API，依次保存校园平台凭据、自动创建 Canvas Token、从 Chrome 导入 Cookie。

**安装选项：**

```bash
bash install.sh --no-setup          # 只安装，不进入 setup
bash install.sh --skip-playwright   # 跳过 Chromium
```

**手动安装：**

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
sjtu-agent setup
```

### 配置大模型 API

推荐使用交大官方 [致远一号](https://zhiyuan.sjtu.edu.cn)（免费）。运行 `sjtu-agent setup` 自动配置，或手动在 `.env` 中写入：

```bash
ZHIYUAN_API_KEY=你的APIKey
```

默认模型 `deepseek-chat`（DeepSeek V3.2）。也可用 DeepSeek 官方、OpenAI 等其他兼容接口，在 Web 配置页选「自定义」填入即可。

---

## 功能

### 终端对话

```bash
sjtu-agent              # 交互式对话
sjtu-agent doctor       # 查看配置和运行时路径
sjtu-agent update       # 一键更新到最新版本
```

### 多平台 Bot

| 平台 | 启动命令 | 斜杠命令 |
| --- | --- | --- |
| 飞书 | `sjtu-agent feishu-bot` | `/hw` `/list` `/new` `/template` `/aihot` `/help` |
| Telegram | `sjtu-agent telegram-bot` | — |
| 微信 | `sjtu-agent wechat-bot` | — |
| QQ | `sjtu-agent qq-bot` | — |

飞书 Bot 基于 WebSocket 长连接，支持多会话、斜杠命令、多模态（图片/文件/音频）。详见 [平台接入](#平台接入)。

### DDL 聚合

一键拉取 Canvas、AI 好课、中国大学 MOOC、phycai 四个平台的作业 DDL，区分今日截止 / 周内截止 / 远期。

```bash
sjtu-agent ddl
sjtu-agent ddl --canvas-only
```

### 每日报告

自动生成晨间早报（今日课表 + DDL）、午间速报（下午课程）、晚间日报（明日课表 + AI 学习建议），通过飞书 / Telegram 推送。

```bash
sjtu-agent daily-report --test            # 预览
sjtu-agent daily-report --type morning    # 早报
sjtu-agent install-daemons                # 安装定时推送
```

### 作业助手

Canvas 集成 + Claude Code 引擎，`/hw do <序号>` 下载作业 → 分析思路 → 生成 PDF 解答。支持 MATLAB 图表和 LaTeX 排版。

```text
/hw                # 列出作业
/hw do 3           # 分析第 3 个作业
/hw due 7          # 7 天内截止
/hw past           # 历史作业
```

### LaTeX 模板

内置 SJTU 本科毕业论文模板（源自 [sjtug/SJTUThesis](https://github.com/sjtug/SJTUThesis)），支持飞书 Bot 内一键编译。

```text
/template                     # 列出可用模板
/template bachelor-thesis     # 套用论文模板
/template compile             # xelatex 编译 PDF
/template clone <project-id>  # 从 Overleaf 克隆
/template push                # 推送回 Overleaf
```

需要 MiKTeX（Windows: `winget install MiKTeX.MiKTeX`）并安装 ctex 宏包：`mpm --install ctex`。

### AI 资讯

飞书 Bot `/aihot` 命令获取每日 AI 圈精选新闻，按模型 / 产品 / 行业 / 论文 / 技巧分类。数据来源 [aihot.virxact.com](https://aihot.virxact.com)（MIT，无需 Key）。

```text
/aihot                       # 今日 AI 新闻
sjtu-agent aihot             # 终端推送
```

灵感来源：[KKKKhazix/khazix-skills](https://github.com/KKKKhazix/khazix-skills) 的 ai-hot 技能（MIT）。

### Canvas 课程监控

定时检查 Canvas 课程公告、quiz、待办事项，通过飞书 / Telegram / 系统通知推送。

```bash
sjtu-agent canvas-watcher --once --test   # 预览
sjtu-agent canvas-watcher --once          # 推送一次
sjtu-agent install-daemons --services canvas-watcher
```

可在对话中让 Agent 配置监控范围：「只监控 ECE2300」「每 10 分钟查一次」。

### 邮件监控

检查 mail.sjtu.edu.cn 新邮件，通过飞书推送（纯通知，不发送/不删除/不修改）。

```bash
sjtu-agent email-watcher --once
sjtu-agent install-daemons --services email-watcher
```

### MCP 与技能扩展

加载外部 MCP Server 作为额外工具，或创建 prompt-only 技能扩展 Agent 能力。

```bash
sjtu-agent add-mcp-server my-tools --transport stdio --command python --arg server.py
sjtu-agent add-skill my-skill --content-file SKILL.md
```

也可在对话中让 Agent 操作：「添加一个 MCP 服务器」「创建一个技能」。

### 多模态解析

支持 OCR（图片文字提取）、ASR（语音转文字）、PDF 解析。可选安装：

```bash
sjtu-agent install-parse-backends --backend pdf_ocr
sjtu-agent install-parse-backends --backend whisper
```

---

## 配置

### 运行时数据

所有配置和缓存文件存储在平台用户数据目录，首次运行自动从项目根目录迁移旧文件：

| 平台 | 路径 |
|------|------|
| macOS | `~/Library/Application Support/sjtu-agent` |
| Linux | `~/.local/share/sjtu-agent` |
| Windows | `%APPDATA%/sjtu-agent` |

三个核心文件：

- `config.json` — 平台 Token、Cookie、Bot 凭据
- `.env` — jAccount 账号密码、致远一号 API Key
- `agent_config.json` — 大模型配置（已有 `ZHIYUAN_API_KEY` 则不需要）

### 环境变量

| 变量 | 用途 |
|------|------|
| `ZHIYUAN_API_KEY` | 致远一号 LLM API Key |
| `JACCOUNT_USERNAME` | jAccount 学号 |
| `JACCOUNT_PASSWORD` | jAccount 密码 |
| `SJTU_AGENT_HOME` | 覆盖默认运行时数据目录 |
| `SJTU_HOMEWORK_DIR` | 作业文件存放目录 |
| `SJTU_PAPERS_DIR` | LaTeX 论文模板目标目录 |
| `MATLAB_PATH` | MATLAB 可执行文件路径 |

---

## 后台服务

### macOS (launchd)

```bash
sjtu-agent install-daemons                    # 安装全部服务
sjtu-agent install-daemons --services daily-report remind-check
```

服务列表：`web` `daily-report` `remind-check` `canvas-watcher` `telegram-bot` `qq-bot` `feishu-bot` `wechat-bot` `aihot-push`

### Windows

**Task Scheduler**（默认，适合定时任务）：

```powershell
sjtu-agent install-daemons
```

**psmux**（适合常驻进程，无弹窗）：

```powershell
winget install psmux
sjtu-agent install-daemons --backend psmux --services feishu-bot telegram-bot
```

飞书 Bot 还提供桌面 GUI 启动器：双击 `install\launch-feishu.bat` 即可。

### Linux (systemd)

```bash
sjtu-agent install-daemons
```

---

## 平台接入

### 飞书 Bot

1. 在 [open.feishu.cn](https://open.feishu.cn) 创建企业自建应用
2. 添加「机器人」能力，申请权限 `im:message` `im:message.p2p_msg:readonly` `im:message:send_as_bot`
3. 事件订阅切到**长连接**，添加 `im.message.receive_v1` 事件
4. **「版本管理与发布」→ 创建版本 → 申请发布**（否则搜不到 bot）
5. 在 WebUI 飞书卡片中填入 App ID / Secret，启动 Bot
6. 在飞书里搜应用名称发消息，终端日志会显示你的 `open_id`，回填到白名单

详细故障排查见 [docs/feishu-bot-troubleshooting.md](docs/feishu-bot-troubleshooting.md)。

### QQ Bot

登录 [q.qq.com](https://q.qq.com/qqbot/openclaw/) 创建机器人获取 AppID / AppSecret → 对话中让 Agent 调用 `setup_qq` → `sjtu-agent qq-bot` 启动。

---

## 版本

当前版本：**v0.3.1**。发布历史见 [Releases](https://github.com/kuan-er/sjtu-agent/releases)。
