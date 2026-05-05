# SJTU Agent

面向上海交通大学学生的校园助手，提供终端对话、Telegram Bot、提醒守护进程和 MCP Server。

English summary: A deployable Shanghai Jiao Tong University campus assistant with terminal chat, Telegram bot, reminder daemon, and MCP server.

## 安装

推荐直接用一键安装脚本：

macOS / Linux:

```bash
git clone https://github.com/kuan-er/sjtu-agent.git && cd sjtu-agent && bash install.sh
```

Windows PowerShell:

```powershell
git clone https://github.com/kuan-er/sjtu-agent.git; cd sjtu-agent; powershell -ExecutionPolicy Bypass -File .\install.ps1
```

这个脚本会自动完成下面这些步骤：创建或复用 `.venv`、升级 `pip`、安装依赖、安装 Playwright Chromium，并默认直接启动 `sjtu-agent setup`。

如果你只想安装、不想立刻进入 setup，或者想跳过 Chromium 安装：

```bash
# macOS / Linux
git clone https://github.com/kuan-er/sjtu-agent.git && cd sjtu-agent && bash install.sh --no-setup
git clone https://github.com/kuan-er/sjtu-agent.git && cd sjtu-agent && bash install.sh --skip-playwright
```

```powershell
# Windows
git clone https://github.com/kuan-er/sjtu-agent.git; cd sjtu-agent; .\install.ps1 -NoSetup
git clone https://github.com/kuan-er/sjtu-agent.git; cd sjtu-agent; .\install.ps1 -SkipPlaywright
```

手动安装方式仍然可用：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
sjtu-agent setup
```

推荐的首次使用方式是直接运行内置的对话式 setup assistant。它会先保存驱动 Agent 的大模型 API 配置，然后依次检查 Python 依赖、自动安装或校验 Playwright Chromium、引导保存校园平台凭据、尽可能自动创建 Canvas Token、从 Chrome 导入教学平台 Cookie、执行配置体检，并在 macOS 上一并安装 launchd 后台服务，最后还能直接启动主对话。

在 `sjtu-agent setup` 过程中，可以直接用自然语言回答，也可以输入这些快捷命令：`status`、`help`、`skip`、`quit`、`open canvas`、`auto canvas`。

## 配置致远一号 API（推荐）

[致远一号](https://zhiyuan.sjtu.edu.cn) 是上海交通大学官方提供的大模型服务平台，支持 OpenAI 兼容接口，交大师生可免费申请使用。

**接入方式：** 安装完成后直接运行 `sjtu-agent setup`，setup 向导会在第一步引导你输入 API Key 并自动保存，无需手动编辑任何文件。

如需手动配置，在运行时数据目录的 `.env` 文件中填入：

```bash
ZHIYUAN_API_KEY=你的致远一号APIKey
```

Base URL 默认为 `https://models.sjtu.edu.cn/api/v1`，模型默认 `deepseek-chat`（即交大部署的 DeepSeek V3.2），无需额外修改。

可用模型列表：

| 调用名 | 说明 |
|--------|------|
| `deepseek-chat` | DeepSeek V3.2（**默认**）|
| `deepseek-reasoner` | DeepSeek V3.2（推理模式）|
| `glm-5` | GLM 5.0 |
| `minimax` / `minimax-m2.5` | MiniMax M2.5 |
| `qwen3coder` | Qwen3-Coder-30B |
| `qwen3vl` | Qwen3-VL-32B |

**如何申请致远一号 API Key：**

前往 [https://zhiyuan.sjtu.edu.cn](https://zhiyuan.sjtu.edu.cn)，使用 jAccount 登录后在「API 管理」中创建 Key。

---

## 常用命令

```bash
sjtu-agent                # 启动主对话
sjtu-agent setup          # 运行首次配置向导
sjtu-agent doctor         # 查看当前配置状态和运行时路径
sjtu-agent setup-config   # 从浏览器读取 Cookie 并生成 config.json
sjtu-agent login --aihaoke
sjtu-agent ddl --canvas-only
sjtu-agent daily-report --test
sjtu-agent telegram-bot --test
sjtu-agent remind-check --list
sjtu-agent mcp --http --port 8765
sjtu-agent install-daemons
```

也可以直接以模块方式运行：

```bash
python -m sjtu_agent
```

几个常用的 setup 变体：

```bash
sjtu-agent setup
sjtu-agent setup --yes --skip-cookie-import --skip-launchd
sjtu-agent setup --yes --write-daemons-only --output-dir /tmp/sjtu-agent-launchd
```

## macOS 后台服务

在 macOS 上，可以直接用一条命令安装内置 launchd 服务：

```bash
sjtu-agent install-daemons
```

默认会把 LaunchAgent plist 写入 `~/Library/LaunchAgents`，并自动加载到当前用户会话。

- `daily-report`：每天 `22:00` 运行一次
- `remind-check`：每 `60` 秒运行一次
- `telegram-bot`：登录后启动，并由 launchd 保活

常见变体：

```bash
sjtu-agent install-daemons --write-only
sjtu-agent install-daemons --services daily-report remind-check
sjtu-agent install-daemons --daily-report-time 21:30 --remind-interval 120
```

这些后台服务会使用当前选定的 Python 解释器，以运行时数据目录为工作目录，并把日志写到 `~/Library/Application Support/sjtu-agent/logs`。

## 配置说明

最重要的运行时文件有三个：

- `config.json`：平台 Token、Cookie、Telegram 配置
- `.env`：jAccount 和 MOOC 账号密码，以及致远一号 API Key
- `agent_config.json`：大模型提供方、Base URL 和模型名（若已在 `.env` 填写 `ZHIYUAN_API_KEY` 则无需此文件）

对于 Canvas，如果 Playwright 和 jAccount 凭据已经就绪，`sjtu-agent setup` 会优先尝试自动创建并保存 Token；如果自动流程失败，再回退到打开 `https://oc.sjtu.edu.cn/profile/settings` 并让你手动确认一次。

## 运行时数据

安装后的命令默认把运行时文件写到用户数据目录，而不是仓库根目录。

- macOS: `~/Library/Application Support/sjtu-agent`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/sjtu-agent`
- Windows: `%APPDATA%/sjtu-agent`

首次导入包时，如果仓库根目录里已经存在这些旧文件，会自动迁移过去：

- `.env`
- `config.json`
- `agent_config.json`
- `reminders.json`
- `remind_state.json`
- `mysjtu_catalog.json`
- `.schedule_cache.json`

## 发布说明

这个仓库已经具备可安装、可分发的包结构和稳定入口；同时，为了保持现有行为稳定，核心平台适配逻辑仍然保留在顶层模块中。
