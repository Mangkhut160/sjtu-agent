from __future__ import annotations

from typing import Any


DEFAULT_CANVAS_MONITOR_CONFIG = {
    "enabled": True,
    "course_ids": [],
    "course_filters": [],
    "include_announcements": True,
    "include_quizzes": True,
    "include_assignments": False,
    "include_activity": False,
    "interval_seconds": 300,
    "notify_channels": ["system", "telegram", "feishu"],
    "baseline_on_first_run": True,
}

MIN_CANVAS_MONITOR_INTERVAL_SECONDS = 30
ALLOWED_CANVAS_NOTIFY_CHANNELS = {"system", "telegram", "feishu", "wechat"}


def merged_canvas_monitor_config(cfg: dict[str, Any]) -> dict[str, Any]:
    monitor = dict(DEFAULT_CANVAS_MONITOR_CONFIG)
    existing = cfg.get("canvas_monitor") or {}
    if isinstance(existing, dict):
        monitor.update(existing)
    monitor["course_ids"] = _int_list(monitor.get("course_ids"))
    monitor["course_filters"] = _str_list(monitor.get("course_filters"))
    monitor["notify_channels"] = _notify_channels(monitor.get("notify_channels"))
    monitor["interval_seconds"] = max(
        MIN_CANVAS_MONITOR_INTERVAL_SECONDS,
        _int_value(monitor.get("interval_seconds"), DEFAULT_CANVAS_MONITOR_CONFIG["interval_seconds"]),
    )
    return monitor


def update_canvas_monitor_config(
    cfg: dict[str, Any],
    *,
    enabled: bool | None = None,
    interval_seconds: int | None = None,
    interval_minutes: float | None = None,
    course_ids: list[int] | None = None,
    course_filters: list[str] | None = None,
    include_announcements: bool | None = None,
    include_quizzes: bool | None = None,
    include_assignments: bool | None = None,
    include_activity: bool | None = None,
    notify_channels: list[str] | None = None,
    baseline_on_first_run: bool | None = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    monitor = merged_canvas_monitor_config(cfg)
    updated_fields: list[str] = []
    notes: list[str] = []

    def set_field(name: str, value: Any) -> None:
        monitor[name] = value
        updated_fields.append(name)

    if enabled is not None:
        set_field("enabled", bool(enabled))
    if course_ids is not None:
        set_field("course_ids", _int_list(course_ids))
        set_field("course_filters", [])
    if course_filters is not None:
        set_field("course_ids", [])
        set_field("course_filters", _str_list(course_filters))
    if include_announcements is not None:
        set_field("include_announcements", bool(include_announcements))
    if include_quizzes is not None:
        set_field("include_quizzes", bool(include_quizzes))
    if include_assignments is not None:
        set_field("include_assignments", bool(include_assignments))
    if include_activity is not None:
        set_field("include_activity", bool(include_activity))
    if notify_channels is not None:
        set_field("notify_channels", _notify_channels(notify_channels))
    if baseline_on_first_run is not None:
        set_field("baseline_on_first_run", bool(baseline_on_first_run))

    interval_value: int | None = None
    if interval_minutes is not None:
        interval_value = int(float(interval_minutes) * 60)
    elif interval_seconds is not None:
        interval_value = int(interval_seconds)
    if interval_value is not None:
        clamped = max(MIN_CANVAS_MONITOR_INTERVAL_SECONDS, interval_value)
        if clamped != interval_value:
            notes.append(f"检查间隔已限制为最小 {MIN_CANVAS_MONITOR_INTERVAL_SECONDS} 秒。")
        set_field("interval_seconds", clamped)

    if monitor.get("course_ids") and monitor.get("course_filters"):
        notes.append("course_ids 优先于 course_filters；两者同时存在时只按 course_ids 选择课程。")
    notes.append("正在运行的 canvas-watcher 会在下一轮循环读取新配置；如需立刻生效请重启 watcher。")

    cfg["canvas_monitor"] = monitor
    return monitor, updated_fields, notes


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_list(values: Any) -> list[int]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    result: list[int] = []
    for value in values:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _str_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    return [str(value).strip() for value in values if str(value).strip()]


def _notify_channels(values: Any) -> list[str]:
    channels = [channel.lower() for channel in _str_list(values)]
    filtered = [channel for channel in channels if channel in ALLOWED_CANVAS_NOTIFY_CHANNELS]
    return filtered or list(DEFAULT_CANVAS_MONITOR_CONFIG["notify_channels"])
