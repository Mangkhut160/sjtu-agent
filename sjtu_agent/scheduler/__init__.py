"""
sjtu_agent/scheduler — 跨平台后台守护进程调度层

根据当前操作系统自动选择合适的实现：
  - macOS   → launchd（plist + launchctl）
  - Windows → Task Scheduler（schtasks 命令行）
  - Linux   → systemd 用户单元（systemctl --user）

公共接口：
  install_daemons(...)   安装并启动后台服务
  uninstall_daemons(...) 停止并卸载后台服务
  daemon_status(...)     查询后台服务状态
"""

from __future__ import annotations

import sys
from pathlib import Path


def install_daemons(
    service_names: tuple[str, ...] | None = None,
    python_executable: Path | None = None,
    daily_report_time: tuple[int, int] = (22, 0),
    remind_interval: int = 60,
    telegram_throttle: int = 10,
    load: bool = True,
    **platform_kwargs,
) -> dict:
    """
    安装后台守护进程。

    参数：
        service_names       要安装的服务子集，默认全部（daily-report / remind-check / telegram-bot）
        python_executable   使用的 Python 解释器路径，默认当前解释器
        daily_report_time   日报发送时间 (hour, minute)，默认 (22, 0)
        remind_interval     提醒检查间隔秒数（macOS/Linux 适用），默认 60
        telegram_throttle   Telegram bot 重启节流秒数（macOS 适用），默认 10
        load                是否立即加载/启动服务，默认 True
        **platform_kwargs   各平台专属参数（如 macOS 的 output_dir）

    返回包含安装结果的字典。
    """
    if sys.platform == "darwin":
        from sjtu_agent.scheduler.launchd import install as _install
    elif sys.platform == "win32":
        from sjtu_agent.scheduler.taskschd import install as _install
    elif sys.platform.startswith("linux"):
        from sjtu_agent.scheduler.systemd import install as _install
    else:
        raise RuntimeError(
            f"不支持的平台: {sys.platform}。"
            "目前支持 macOS (darwin)、Windows (win32)、Linux。"
        )

    return _install(
        service_names=service_names,
        python_executable=python_executable,
        daily_report_time=daily_report_time,
        remind_interval=remind_interval,
        telegram_throttle=telegram_throttle,
        load=load,
        **platform_kwargs,
    )


def uninstall_daemons(
    service_names: tuple[str, ...] | None = None,
    **platform_kwargs,
) -> dict:
    """
    卸载后台守护进程。

    参数：
        service_names  要卸载的服务子集，默认全部
    """
    if sys.platform == "darwin":
        from sjtu_agent.scheduler.launchd import uninstall as _uninstall
    elif sys.platform == "win32":
        from sjtu_agent.scheduler.taskschd import uninstall as _uninstall
    elif sys.platform.startswith("linux"):
        from sjtu_agent.scheduler.systemd import uninstall as _uninstall
    else:
        raise RuntimeError(f"不支持的平台: {sys.platform}")

    return _uninstall(service_names=service_names, **platform_kwargs)


def daemon_status(
    service_names: tuple[str, ...] | None = None,
    **platform_kwargs,
) -> dict:
    """
    查询后台守护进程状态。

    返回包含各服务状态的字典。
    """
    if sys.platform == "darwin":
        from sjtu_agent.scheduler.launchd import status as _status
    elif sys.platform == "win32":
        from sjtu_agent.scheduler.taskschd import status as _status
    elif sys.platform.startswith("linux"):
        from sjtu_agent.scheduler.systemd import status as _status
    else:
        return {"error": f"不支持的平台: {sys.platform}", "services": []}

    return _status(service_names=service_names, **platform_kwargs)


def available_service_names() -> tuple[str, ...]:
    """返回所有可用的服务名称。"""
    return ("daily-report", "remind-check", "telegram-bot", "wechat-bot", "feishu-bot")


def current_platform_name() -> str:
    """返回当前平台的友好名称。"""
    if sys.platform == "darwin":
        return "macOS (launchd)"
    elif sys.platform == "win32":
        return "Windows (Task Scheduler)"
    elif sys.platform.startswith("linux"):
        return "Linux (systemd)"
    else:
        return sys.platform
