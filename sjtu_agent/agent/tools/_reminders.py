"""Reminder tools — add, list, remove local reminders."""

import json
import datetime as _dt

from sjtu_agent.paths import REMINDERS_PATH

import ddl_checker as dc

# ── TOOLS schema entries ──────────────────────────────────────────────────────

TOOLS_ENTRIES = [
    {
        "type": "function",
        "function": {
            "name": "add_reminder",
            "description": (
                "添加一条提醒事项到本地列表。"
                "用户说「帮我记一下」「提醒我」「记得要...」「把 XXX 加到提醒」时调用。"
                "start 是提醒开始时间（或事项截止时间），end 是可选的结束时间。"
                "若用户未提供具体时间，从上下文推断或询问。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "提醒标题，简洁描述事项"},
                    "start": {"type": "string", "description": "开始时间，格式 'YYYY-MM-DD HH:MM'"},
                    "end":   {"type": "string", "description": "结束时间（可选），格式 'YYYY-MM-DD HH:MM'"},
                    "note":  {"type": "string", "description": "备注说明（可选）"},
                },
                "required": ["title", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": (
                "查看所有提醒事项（分为未过期/已过期）。"
                "用户说「我有什么提醒」「提醒事项」「记了什么」时调用。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_reminder",
            "description": "删除指定 id 的提醒事项。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_id": {"type": "integer", "description": "要删除的提醒 id"},
                },
                "required": ["reminder_id"],
            },
        },
    },
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_reminders() -> list[dict]:
    if not REMINDERS_PATH.exists():
        return []
    try:
        return json.loads(REMINDERS_PATH.read_text(encoding="utf-8")).get("reminders", [])
    except Exception:
        return []


def _save_reminders(reminders: list[dict]) -> None:
    REMINDERS_PATH.write_text(
        json.dumps({"reminders": reminders}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── tool implementations ──────────────────────────────────────────────────────

def tool_add_reminder(
    title: str,
    start: str,
    end: str = "",
    note: str = "",
) -> dict:
    def _parse(s: str) -> _dt.datetime | None:
        if not s:
            return None
        s = s.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = _dt.datetime.strptime(s, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dc.CST)
                return dt
            except ValueError:
                continue
        return None

    start_dt = _parse(start)
    if start_dt is None:
        return {"error": f"无法解析时间：{start!r}，请使用 'YYYY-MM-DD HH:MM' 格式"}

    reminders = _load_reminders()
    new_id = max((r["id"] for r in reminders), default=0) + 1
    entry = {
        "id":    new_id,
        "title": title.strip(),
        "start": start_dt.isoformat(),
        "end":   _parse(end).isoformat() if end else "",
        "note":  note.strip(),
    }
    reminders.append(entry)
    _save_reminders(reminders)
    return {"ok": True, "id": new_id, "reminder": entry}


def tool_list_reminders() -> dict:
    now = _dt.datetime.now(dc.CST)
    reminders = _load_reminders()
    items = []
    for r in reminders:
        end_str = r.get("end", "")
        expired = False
        if end_str:
            try:
                end_dt = _dt.datetime.fromisoformat(end_str)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=dc.CST)
                expired = end_dt < now
            except Exception:
                pass
        items.append({**r, "expired": expired})
    active   = [i for i in items if not i["expired"]]
    inactive = [i for i in items if i["expired"]]
    return {
        "current_time": now.strftime("%Y-%m-%d %H:%M"),
        "active_count": len(active),
        "active":   active,
        "expired":  inactive,
    }


def tool_remove_reminder(reminder_id: int) -> dict:
    reminders = _load_reminders()
    new_list = [r for r in reminders if r["id"] != reminder_id]
    if len(new_list) == len(reminders):
        return {"error": f"未找到 id={reminder_id} 的提醒事项"}
    _save_reminders(new_list)
    return {"ok": True, "removed_id": reminder_id}
