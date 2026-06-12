# Canvas Course Query and Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `sjtu-agent` 增加 Canvas 单课程查询工具，并在同一套 Canvas 能力层之上实现可定时运行的 Canvas watcher。

**Architecture:** 新增 `sjtu_agent/canvas_client.py` 作为只读 Canvas API 能力层，Agent tools 和 watcher 都复用它。第一阶段暴露课程、公告、quiz、课程更新、todo 查询工具；第二阶段新增 `scripts/canvas_watcher.py`、共享通知模块和 scheduler/CLI 注册。

**Tech Stack:** Python 3.10+ 风格代码、`requests`、现有 `pytest` 测试框架、现有 `sjtu_agent.paths` runtime 路径、现有 argparse CLI 和 scheduler 后端。

---

## 设计输入

实现依据：

- 设计文档：`docs/superpowers/specs/2026-06-12-canvas-course-monitor-design.md`
- 当前 Canvas DDL 实现：`ddl_checker.py`
- 当前 Agent tool 注册：`sjtu_agent/agent/tools/_core.py`
- 当前 CLI 注册：`sjtu_agent/cli.py`
- 当前后台服务注册：`sjtu_agent/scheduler/__init__.py`、`sjtu_agent/scheduler/launchd.py`、`sjtu_agent/scheduler/taskschd.py`、`sjtu_agent/scheduler/systemd.py`、`sjtu_agent/scheduler/psmuxd.py`

## 文件结构

新增文件：

- `sjtu_agent/canvas_client.py`：Canvas API client、课程解析、公告/quiz/作业/activity/todo 规范化。
- `sjtu_agent/notifications.py`：可复用通知发送入口，供 watcher 使用；第一版复用系统通知、Telegram、飞书，WeChat 只在安全 helper 可用时接入。
- `scripts/canvas_watcher.py`：Canvas 定时监控脚本，支持 `--once`、`--test`。
- `tests/test_canvas_client.py`：Canvas client 的 mock 单元测试。
- `tests/test_canvas_tools.py`：Agent tools 的 mock 单元测试。
- `tests/test_canvas_watcher.py`：watcher 状态和事件检测测试。

修改文件：

- `sjtu_agent/agent/tools/_core.py`：加入 Canvas 查询 tools 定义、实现函数和 `run_tool` dispatch。
- `sjtu_agent/agent/__init__.py`：导出新增 tool 函数。
- `sjtu_agent/paths.py`：新增 `CANVAS_MONITOR_STATE_PATH`。
- `sjtu_agent/cli.py`：新增 `canvas-watcher` 子命令。
- `sjtu_agent/scheduler/__init__.py`：把 `canvas-watcher` 加入可用服务。
- `sjtu_agent/scheduler/launchd.py`：注册 macOS launchd service。
- `sjtu_agent/scheduler/taskschd.py`：注册 Windows Task Scheduler service。
- `sjtu_agent/scheduler/systemd.py`：注册 Linux systemd service。
- `sjtu_agent/scheduler/psmuxd.py`：注册 Windows psmux service。
- `README.md` 和 `README_EN.md`：补充新工具、CLI 和配置说明。

## Task 1: Canvas Client 测试

**Files:**

- Create: `tests/test_canvas_client.py`

- [ ] **Step 1: 写 Canvas client 的失败测试**

创建 `tests/test_canvas_client.py`，包含下面测试骨架。测试使用 fake session，不访问真实 Canvas。

```python
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from sjtu_agent.canvas_client import CanvasClient, CanvasError


@dataclass
class FakeResponse:
    status_code: int
    payload: object
    links: dict | None = None
    text: str = ""
    headers: dict | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, routes: dict[tuple[str, str], FakeResponse]):
        self.routes = routes
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=15):
        path = url.replace("https://oc.sjtu.edu.cn", "")
        key = (path, json.dumps(params or {}, sort_keys=True, ensure_ascii=False))
        self.calls.append((path, params or {}))
        if key not in self.routes:
            raise AssertionError(f"unexpected GET {path} {params}")
        response = self.routes[key]
        response.links = response.links or {}
        response.headers = response.headers or {"content-type": "application/json"}
        return response


def key(path: str, params: dict | None = None) -> tuple[str, str]:
    return (path, json.dumps(params or {}, sort_keys=True, ensure_ascii=False))


def client(routes: dict[tuple[str, str], FakeResponse]) -> CanvasClient:
    return CanvasClient(base_url="https://oc.sjtu.edu.cn", token="tok", session=FakeSession(routes))


def test_list_courses_normalizes_active_courses():
    c = client({
        key("/api/v1/courses", {"enrollment_state": "active", "enrollment_type": "student", "per_page": 100}): FakeResponse(200, [
            {"id": 92355, "name": "ECE2300JSU2026-1", "course_code": "ECE2300JSU2026-1", "workflow_state": "available", "default_view": "wiki"},
            {"id": 87450, "name": "马克思主义基本原理", "course_code": "本-(2025)-MARX1204", "workflow_state": "available"},
        ])
    })

    result = c.list_courses()

    assert result["count"] == 2
    assert result["courses"][0]["course_id"] == 92355
    assert result["courses"][0]["name"] == "ECE2300JSU2026-1"
    assert result["courses"][1]["course_code"] == "本-(2025)-MARX1204"


def test_resolve_course_by_id_name_and_ambiguous_match():
    c = client({
        key("/api/v1/courses", {"enrollment_state": "active", "enrollment_type": "student", "per_page": 100}): FakeResponse(200, [
            {"id": 1, "name": "Linear Algebra", "course_code": "MATH2030"},
            {"id": 2, "name": "Linear Systems", "course_code": "ECE2300"},
        ])
    })

    assert c.resolve_course("1")["course"]["course_id"] == 1
    assert c.resolve_course("MATH2030")["course"]["course_id"] == 1

    ambiguous = c.resolve_course("linear")
    assert ambiguous["ok"] is False
    assert ambiguous["error"] == "ambiguous_course"
    assert [item["course_id"] for item in ambiguous["candidates"]] == [1, 2]


def test_announcements_use_context_codes_and_normalize_html_summary():
    c = client({
        key("/api/v1/announcements", {"context_codes[]": "course_92355", "per_page": 20}): FakeResponse(200, [
            {
                "id": 10,
                "title": "Exam",
                "message": "<p>Midterm moved</p>",
                "posted_at": "2026-06-01T02:00:00Z",
                "html_url": "https://oc.sjtu.edu.cn/courses/92355/discussion_topics/10",
                "author": {"display_name": "Teacher"},
            }
        ])
    })

    result = c.list_announcements(92355, limit=20)

    assert result["count"] == 1
    assert result["announcements"][0]["id"] == 10
    assert result["announcements"][0]["summary"] == "Midterm moved"
    assert result["announcements"][0]["author"] == "Teacher"


def test_classic_quizzes_success_and_assignment_supplement():
    c = client({
        key("/api/v1/courses/92355/quizzes", {"per_page": 100}): FakeResponse(200, [
            {"id": 77327, "assignment_id": 410908, "title": "Quiz 0", "due_at": None, "lock_at": "2026-05-18T12:00:00Z", "html_url": "https://oc/quizzes/77327"}
        ]),
        key("/api/v1/courses/92355/assignments", {"per_page": 100, "order_by": "due_at"}): FakeResponse(200, [
            {"id": 410908, "name": "Quiz 0", "quiz_id": 77327, "submission_types": ["online_quiz"], "due_at": None, "html_url": "https://oc/assignments/410908"},
            {"id": 410909, "name": "Quiz 1", "submission_types": ["online_quiz"], "due_at": "2026-06-20T15:59:00Z", "html_url": "https://oc/assignments/410909"},
        ]),
    })

    result = c.list_quizzes(92355)

    assert result["quiz_status"] == "enabled"
    assert result["count"] == 2
    assert result["quizzes"][0]["quiz_id"] == 77327
    assert result["quizzes"][1]["source"] == "assignment"
    assert result["quizzes"][1]["assignment_id"] == 410909


def test_classic_quizzes_disabled_is_not_fatal():
    c = client({
        key("/api/v1/courses/87450/quizzes", {"per_page": 100}): FakeResponse(404, {"message": "该页面已对此课程禁用"}),
        key("/api/v1/courses/87450/assignments", {"per_page": 100, "order_by": "due_at"}): FakeResponse(200, []),
    })

    result = c.list_quizzes(87450)

    assert result["ok"] is True
    assert result["quiz_status"] == "disabled"
    assert result["count"] == 0
    assert "禁用" in result["warnings"][0]


def test_course_updates_keeps_partial_results_when_activity_fails():
    c = client({
        key("/api/v1/announcements", {"context_codes[]": "course_1", "per_page": 5}): FakeResponse(200, []),
        key("/api/v1/courses/1/quizzes", {"per_page": 100}): FakeResponse(404, {"message": "该页面已对此课程禁用"}),
        key("/api/v1/courses/1/assignments", {"per_page": 100, "order_by": "due_at"}): FakeResponse(200, []),
        key("/api/v1/courses/1/activity_stream", {"per_page": 5}): FakeResponse(500, {"message": "boom"}),
    })

    result = c.get_course_updates(1, include=["announcements", "quizzes", "activity"], limit=5)

    assert result["ok"] is True
    assert "announcements" in result["sections"]
    assert "quizzes" in result["sections"]
    assert result["warnings"]


def test_missing_token_raises_canvas_error():
    with pytest.raises(CanvasError) as exc:
        CanvasClient(base_url="https://oc.sjtu.edu.cn", token="")

    assert exc.value.code == "missing_token"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_canvas_client.py -q
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'sjtu_agent.canvas_client'`。

- [ ] **Step 3: 提交失败测试**

```bash
git add tests/test_canvas_client.py
git commit -m "test: cover canvas client behavior"
```

## Task 2: Canvas Client 实现

**Files:**

- Create: `sjtu_agent/canvas_client.py`
- Test: `tests/test_canvas_client.py`

- [ ] **Step 1: 实现 `sjtu_agent/canvas_client.py`**

创建模块，核心形状如下。执行时保持函数名、返回字段和异常 code 与测试一致。

```python
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any

import requests

from sjtu_agent.paths import CONFIG_PATH, read_json_safe

DEFAULT_CANVAS_BASE_URL = "https://oc.sjtu.edu.cn"
CST = timezone(timedelta(hours=8))


class CanvasError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_dict(self) -> dict:
        data = {"ok": False, "error": self.code, "message": self.message}
        if self.status_code is not None:
            data["status_code"] = self.status_code
        return data


def load_canvas_config() -> dict:
    cfg = read_json_safe(CONFIG_PATH, default={})
    return {
        "base_url": (cfg.get("canvas_base_url") or DEFAULT_CANVAS_BASE_URL).rstrip("/"),
        "token": (cfg.get("canvas_token") or "").strip(),
    }


def make_client_from_config() -> "CanvasClient":
    cfg = load_canvas_config()
    return CanvasClient(base_url=cfg["base_url"], token=cfg["token"])


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _truncate(value: str, limit: int = 240) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]


def _is_disabled_message(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    message = str(payload.get("message") or "")
    lowered = message.lower()
    return "禁用" in message or "disabled" in lowered


class CanvasClient:
    def __init__(self, base_url: str, token: str, session: requests.Session | None = None, timeout: int = 15):
        self.base_url = (base_url or DEFAULT_CANVAS_BASE_URL).rstrip("/")
        self.token = (token or "").strip()
        self.timeout = timeout
        if not self.token or self.token.startswith("YOUR_"):
            raise CanvasError("missing_token", "未配置 Canvas Token，请先运行 setup_canvas。")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.token}", "Accept": "application/json"})

    def _get_json(self, path: str, params: dict | None = None) -> tuple[object, Any]:
        try:
            response = self.session.get(f"{self.base_url}{path}", params=params or {}, timeout=self.timeout)
        except requests.Timeout as exc:
            raise CanvasError("timeout", f"Canvas 请求超时: {path}") from exc
        except requests.RequestException as exc:
            raise CanvasError("request_error", f"Canvas 请求失败: {exc}") from exc

        try:
            payload = response.json()
        except Exception as exc:
            raise CanvasError("invalid_json", f"Canvas 返回不是 JSON: {path}", status_code=response.status_code) from exc

        if response.status_code in (401, 403):
            raise CanvasError("invalid_token", "Canvas Token 无效或权限不足，请重新运行 setup_canvas。", status_code=response.status_code)
        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, dict) else str(payload)
            raise CanvasError("http_error", str(message or "Canvas 请求失败"), status_code=response.status_code)
        return payload, response

    def _get_list_page(self, path: str, params: dict | None = None) -> tuple[list, Any]:
        payload, response = self._get_json(path, params=params)
        if not isinstance(payload, list):
            raise CanvasError("unexpected_schema", f"Canvas list endpoint returned {type(payload).__name__}: {path}")
        return payload, response

    def _get_all_pages(self, path: str, params: dict | None = None, max_pages: int = 20) -> list:
        items: list = []
        next_url: str | None = f"{self.base_url}{path}"
        current_params = params or {}
        pages = 0
        while next_url and pages < max_pages:
            response = self.session.get(next_url, params=current_params, timeout=self.timeout)
            try:
                payload = response.json()
            except Exception as exc:
                raise CanvasError("invalid_json", f"Canvas 返回不是 JSON: {path}", status_code=response.status_code) from exc
            if response.status_code >= 400:
                message = payload.get("message") if isinstance(payload, dict) else str(payload)
                raise CanvasError("http_error", str(message or "Canvas 请求失败"), status_code=response.status_code)
            if not isinstance(payload, list):
                raise CanvasError("unexpected_schema", f"Canvas list endpoint returned {type(payload).__name__}: {path}")
            items.extend(payload)
            next_url = response.links.get("next", {}).get("url") if hasattr(response, "links") else None
            current_params = {}
            pages += 1
        return items

    def list_courses(self, include_tabs: bool = False, include_teachers: bool = False) -> dict:
        raw_courses = self._get_all_pages("/api/v1/courses", {
            "enrollment_state": "active",
            "enrollment_type": "student",
            "per_page": 100,
        })
        courses = [self._normalize_course(c) for c in raw_courses if isinstance(c, dict) and c.get("id")]
        for course in courses:
            cid = course["course_id"]
            if include_tabs:
                course["tabs"] = self.list_tabs(cid).get("tabs", [])
            if include_teachers:
                course["teachers"] = self.list_teachers(cid).get("teachers", [])
        return {"ok": True, "count": len(courses), "courses": courses}

    def resolve_course(self, query: str | int) -> dict:
        courses = self.list_courses()["courses"]
        text = str(query).strip()
        if text.isdigit():
            cid = int(text)
            for course in courses:
                if course["course_id"] == cid:
                    return {"ok": True, "course": course}
        lowered = text.lower()
        exact = [c for c in courses if c.get("name") == text or str(c.get("course_code", "")).lower() == lowered]
        if len(exact) == 1:
            return {"ok": True, "course": exact[0]}
        matches = [
            c for c in courses
            if lowered in str(c.get("name", "")).lower() or lowered in str(c.get("course_code", "")).lower()
        ]
        if len(matches) == 1:
            return {"ok": True, "course": matches[0]}
        if len(matches) > 1:
            return {"ok": False, "error": "ambiguous_course", "query": text, "candidates": matches}
        return {"ok": False, "error": "course_not_found", "query": text, "sample_courses": courses[:10]}

    def get_course(self, course_id: int) -> dict:
        payload, _ = self._get_json(f"/api/v1/courses/{course_id}", {"include[]": ["term", "teachers", "syllabus_body"]})
        if not isinstance(payload, dict):
            raise CanvasError("unexpected_schema", "课程详情不是对象")
        return {"ok": True, "course": self._normalize_course(payload)}

    def list_tabs(self, course_id: int) -> dict:
        payload, _ = self._get_list_page(f"/api/v1/courses/{course_id}/tabs")
        tabs = [{"id": t.get("id"), "label": t.get("label"), "html_url": t.get("html_url"), "type": t.get("type")} for t in payload if isinstance(t, dict)]
        return {"ok": True, "count": len(tabs), "tabs": tabs}

    def list_teachers(self, course_id: int) -> dict:
        payload, _ = self._get_list_page(f"/api/v1/courses/{course_id}/users", {"enrollment_type[]": "teacher", "per_page": 100})
        teachers = [{"id": t.get("id"), "name": t.get("name"), "sortable_name": t.get("sortable_name")} for t in payload if isinstance(t, dict)]
        return {"ok": True, "count": len(teachers), "teachers": teachers}

    def list_announcements(self, course_id: int, limit: int = 20, since_days: int | None = None) -> dict:
        params: dict = {"context_codes[]": f"course_{course_id}", "per_page": max(1, min(int(limit or 20), 100))}
        payload, _ = self._get_list_page("/api/v1/announcements", params)
        announcements = [self._normalize_announcement(item) for item in payload if isinstance(item, dict)]
        if since_days is not None:
            cutoff = datetime.now(CST) - timedelta(days=max(0, int(since_days)))
            announcements = [a for a in announcements if _parse_dt(a.get("posted_at")) is None or _parse_dt(a.get("posted_at")) >= cutoff]
        return {"ok": True, "count": len(announcements), "announcements": announcements[:limit]}

    def list_assignments(self, course_id: int, include_past: bool = True) -> dict:
        payload = self._get_all_pages(f"/api/v1/courses/{course_id}/assignments", {"per_page": 100, "order_by": "due_at"})
        assignments = [self._normalize_assignment(item) for item in payload if isinstance(item, dict)]
        if not include_past:
            now = datetime.now(CST)
            assignments = [a for a in assignments if _parse_dt(a.get("due_at")) is None or _parse_dt(a.get("due_at")) >= now]
        return {"ok": True, "count": len(assignments), "assignments": assignments}

    def list_quizzes(self, course_id: int, include_past: bool = True, include_assignment_backed: bool = True) -> dict:
        warnings: list[str] = []
        quiz_status = "unknown"
        quizzes: list[dict] = []
        try:
            payload, _ = self._get_list_page(f"/api/v1/courses/{course_id}/quizzes", {"per_page": 100})
            quiz_status = "enabled"
            quizzes = [self._normalize_quiz(item) for item in payload if isinstance(item, dict)]
        except CanvasError as exc:
            if exc.status_code == 404 and ("禁用" in exc.message or "disabled" in exc.message.lower()):
                quiz_status = "disabled"
                warnings.append(exc.message)
            else:
                warnings.append(exc.message)

        if include_assignment_backed:
            assignments = self.list_assignments(course_id, include_past=include_past).get("assignments", [])
            quizzes = _merge_assignment_backed_quizzes(quizzes, assignments)

        if not include_past:
            now = datetime.now(CST)
            quizzes = [q for q in quizzes if _parse_dt(q.get("due_at")) is None or _parse_dt(q.get("due_at")) >= now]
        return {"ok": True, "quiz_status": quiz_status, "count": len(quizzes), "quizzes": quizzes, "warnings": warnings}

    def list_activity(self, course_id: int, limit: int = 10) -> dict:
        payload, _ = self._get_list_page(f"/api/v1/courses/{course_id}/activity_stream", {"per_page": max(1, min(int(limit or 10), 100))})
        items = [self._normalize_activity(item) for item in payload if isinstance(item, dict)]
        return {"ok": True, "count": len(items), "activity": items[:limit]}

    def list_todo(self, limit: int = 20) -> dict:
        todo, _ = self._get_list_page("/api/v1/users/self/todo")
        planner, _ = self._get_list_page("/api/v1/planner/items", {"per_page": max(1, min(int(limit or 20), 100))})
        items = [self._normalize_todo(item) for item in todo if isinstance(item, dict)]
        items.extend(self._normalize_planner_item(item) for item in planner if isinstance(item, dict))
        return {"ok": True, "count": len(items[:limit]), "items": items[:limit]}

    def list_planner_items(self, limit: int = 20) -> dict:
        payload, _ = self._get_list_page("/api/v1/planner/items", {"per_page": max(1, min(int(limit or 20), 100))})
        items = [self._normalize_planner_item(item) for item in payload if isinstance(item, dict)]
        return {"ok": True, "count": len(items), "items": items[:limit]}

    def get_course_updates(self, course_id: int, include: list[str] | None = None, limit: int = 10) -> dict:
        include = include or ["announcements", "quizzes", "assignments", "activity"]
        sections: dict[str, object] = {}
        warnings: list[str] = []
        for name in include:
            try:
                if name == "announcements":
                    sections[name] = self.list_announcements(course_id, limit=limit)
                elif name == "quizzes":
                    sections[name] = self.list_quizzes(course_id)
                elif name == "assignments":
                    sections[name] = self.list_assignments(course_id)
                elif name == "activity":
                    sections[name] = self.list_activity(course_id, limit=limit)
            except CanvasError as exc:
                warnings.append(f"{name}: {exc.message}")
        return {"ok": True, "course_id": course_id, "sections": sections, "warnings": warnings}

    def _normalize_course(self, course: dict) -> dict:
        return {
            "course_id": course.get("id"),
            "name": course.get("name") or course.get("course_code") or f"课程{course.get('id')}",
            "course_code": course.get("course_code", ""),
            "workflow_state": course.get("workflow_state", ""),
            "default_view": course.get("default_view", ""),
        }

    def _normalize_announcement(self, item: dict) -> dict:
        message = item.get("message") or ""
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        return {
            "id": item.get("id"),
            "title": item.get("title", ""),
            "posted_at": item.get("posted_at") or item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "author": author.get("display_name", ""),
            "summary": _truncate(_strip_html(message)),
            "message_hash": _hash_text(_strip_html(message)),
            "html_url": item.get("html_url", ""),
            "read_state": item.get("read_state"),
        }

    def _normalize_assignment(self, item: dict) -> dict:
        return {
            "assignment_id": item.get("id"),
            "name": item.get("name", ""),
            "due_at": item.get("due_at"),
            "unlock_at": item.get("unlock_at"),
            "lock_at": item.get("lock_at"),
            "published": item.get("published"),
            "locked_for_user": item.get("locked_for_user"),
            "points_possible": item.get("points_possible"),
            "submission_types": item.get("submission_types") or [],
            "quiz_id": item.get("quiz_id") or item.get("original_quiz_id"),
            "is_quiz_assignment": bool(item.get("is_quiz_assignment") or item.get("quiz_id") or item.get("original_quiz_id") or "online_quiz" in (item.get("submission_types") or [])),
            "html_url": item.get("html_url", ""),
        }

    def _normalize_quiz(self, item: dict) -> dict:
        return {
            "source": "quiz",
            "quiz_id": item.get("id"),
            "assignment_id": item.get("assignment_id"),
            "title": item.get("title", ""),
            "quiz_type": item.get("quiz_type"),
            "unlock_at": item.get("unlock_at"),
            "due_at": item.get("due_at"),
            "lock_at": item.get("lock_at"),
            "time_limit": item.get("time_limit"),
            "allowed_attempts": item.get("allowed_attempts"),
            "question_count": item.get("question_count"),
            "points_possible": item.get("points_possible"),
            "published": item.get("published"),
            "locked_for_user": item.get("locked_for_user"),
            "html_url": item.get("html_url", ""),
        }

    def _normalize_activity(self, item: dict) -> dict:
        return {
            "id": item.get("id"),
            "type": item.get("type"),
            "title": item.get("title", ""),
            "message": _truncate(_strip_html(item.get("message", ""))),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "html_url": item.get("html_url", ""),
            "read_state": item.get("read_state"),
        }

    def _normalize_todo(self, item: dict) -> dict:
        assignment = item.get("assignment") if isinstance(item.get("assignment"), dict) else {}
        return {
            "source": "todo",
            "type": item.get("type"),
            "course_id": item.get("course_id"),
            "context_name": item.get("context_name"),
            "title": assignment.get("name") or item.get("type"),
            "html_url": item.get("html_url"),
            "due_at": assignment.get("due_at"),
        }

    def _normalize_planner_item(self, item: dict) -> dict:
        plannable = item.get("plannable") if isinstance(item.get("plannable"), dict) else {}
        return {
            "source": "planner",
            "type": item.get("plannable_type"),
            "course_id": item.get("course_id"),
            "context_name": item.get("context_name"),
            "title": plannable.get("title") or plannable.get("name") or item.get("plannable_type"),
            "html_url": item.get("html_url"),
            "due_at": item.get("plannable_date"),
            "new_activity": item.get("new_activity"),
        }


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(CST)
    except Exception:
        return None


def _merge_assignment_backed_quizzes(quizzes: list[dict], assignments: list[dict]) -> list[dict]:
    seen_quiz_ids = {q.get("quiz_id") for q in quizzes if q.get("quiz_id")}
    seen_assignment_ids = {q.get("assignment_id") for q in quizzes if q.get("assignment_id")}
    merged = list(quizzes)
    for assignment in assignments:
        if not assignment.get("is_quiz_assignment"):
            continue
        quiz_id = assignment.get("quiz_id")
        assignment_id = assignment.get("assignment_id")
        if quiz_id and quiz_id in seen_quiz_ids:
            continue
        if assignment_id and assignment_id in seen_assignment_ids:
            continue
        merged.append({
            "source": "assignment",
            "quiz_id": quiz_id,
            "assignment_id": assignment_id,
            "title": assignment.get("name", ""),
            "quiz_type": "assignment",
            "unlock_at": assignment.get("unlock_at"),
            "due_at": assignment.get("due_at"),
            "lock_at": assignment.get("lock_at"),
            "time_limit": None,
            "allowed_attempts": None,
            "question_count": None,
            "points_possible": assignment.get("points_possible"),
            "published": assignment.get("published"),
            "locked_for_user": assignment.get("locked_for_user"),
            "html_url": assignment.get("html_url", ""),
        })
    return merged
```

- [ ] **Step 2: 运行 client 测试**

Run:

```bash
pytest tests/test_canvas_client.py -q
```

Expected: PASS。

- [ ] **Step 3: 提交 Canvas client**

```bash
git add sjtu_agent/canvas_client.py tests/test_canvas_client.py
git commit -m "feat: add canvas client"
```

## Task 3: Agent Canvas Tools 测试

**Files:**

- Create: `tests/test_canvas_tools.py`

- [ ] **Step 1: 写 Agent tools 的失败测试**

创建 `tests/test_canvas_tools.py`：

```python
from __future__ import annotations

import json

import sjtu_agent.agent.tools as tools


class FakeCanvasClient:
    def __init__(self):
        self.calls = []

    def list_courses(self, include_tabs=False, include_teachers=False):
        self.calls.append(("list_courses", include_tabs, include_teachers))
        return {"ok": True, "count": 1, "courses": [{"course_id": 1, "name": "Signals", "course_code": "ECE2300"}]}

    def resolve_course(self, course):
        if str(course) == "many":
            return {"ok": False, "error": "ambiguous_course", "candidates": [{"course_id": 1, "name": "A"}, {"course_id": 2, "name": "B"}]}
        return {"ok": True, "course": {"course_id": 1, "name": "Signals", "course_code": "ECE2300"}}

    def list_announcements(self, course_id, limit=20, since_days=None):
        self.calls.append(("announcements", course_id, limit, since_days))
        return {"ok": True, "count": 1, "announcements": [{"id": 10, "title": "Exam"}]}

    def list_quizzes(self, course_id, include_past=True, include_assignment_backed=True):
        self.calls.append(("quizzes", course_id, include_past, include_assignment_backed))
        return {"ok": True, "quiz_status": "enabled", "count": 1, "quizzes": [{"quiz_id": 5, "title": "Quiz"}], "warnings": []}

    def get_course_updates(self, course_id, include=None, limit=10):
        self.calls.append(("updates", course_id, include, limit))
        return {"ok": True, "course_id": course_id, "sections": {"announcements": {"count": 0}}, "warnings": []}

    def list_todo(self, limit=20):
        self.calls.append(("todo", limit))
        return {"ok": True, "count": 1, "items": [{"title": "Submit"}]}


def test_list_canvas_courses_tool(monkeypatch):
    fake = FakeCanvasClient()
    monkeypatch.setattr(tools._core, "_make_canvas_client", lambda: fake)

    result = tools.tool_list_canvas_courses(include_tabs=True, include_teachers=True)

    assert result["ok"] is True
    assert result["courses"][0]["name"] == "Signals"
    assert fake.calls == [("list_courses", True, True)]


def test_course_announcements_resolves_course(monkeypatch):
    fake = FakeCanvasClient()
    monkeypatch.setattr(tools._core, "_make_canvas_client", lambda: fake)

    result = tools.tool_get_canvas_course_announcements("ECE2300", limit=5, since_days=3)

    assert result["course"]["course_id"] == 1
    assert result["announcements"][0]["title"] == "Exam"
    assert ("announcements", 1, 5, 3) in fake.calls


def test_course_quizzes_returns_ambiguity_without_fetch(monkeypatch):
    fake = FakeCanvasClient()
    monkeypatch.setattr(tools._core, "_make_canvas_client", lambda: fake)

    result = tools.tool_get_canvas_course_quizzes("many")

    assert result["ok"] is False
    assert result["error"] == "ambiguous_course"
    assert all(call[0] != "quizzes" for call in fake.calls)


def test_run_tool_dispatches_canvas_tools(monkeypatch):
    fake = FakeCanvasClient()
    monkeypatch.setattr(tools._core, "_make_canvas_client", lambda: fake)

    payload = json.loads(tools.run_tool("get_canvas_todo", {"limit": 3}))

    assert payload["ok"] is True
    assert payload["items"][0]["title"] == "Submit"
    assert fake.calls == [("todo", 3)]


def test_tools_catalog_contains_canvas_tools():
    names = {item["function"]["name"] for item in tools.TOOLS if item.get("type") == "function"}

    assert "list_canvas_courses" in names
    assert "get_canvas_course_announcements" in names
    assert "get_canvas_course_quizzes" in names
    assert "get_canvas_course_updates" in names
    assert "get_canvas_todo" in names
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_canvas_tools.py -q
```

Expected: FAIL，错误包含 `AttributeError`，因为工具函数尚未实现。

- [ ] **Step 3: 提交失败测试**

```bash
git add tests/test_canvas_tools.py
git commit -m "test: cover canvas agent tools"
```

## Task 4: Agent Canvas Tools 实现

**Files:**

- Modify: `sjtu_agent/agent/tools/_core.py`
- Modify: `sjtu_agent/agent/__init__.py`
- Test: `tests/test_canvas_tools.py`

- [ ] **Step 1: 在 `_core.py` 导入 Canvas client**

在 `_core.py` imports 后加入：

```python
from sjtu_agent.canvas_client import CanvasError, make_client_from_config
```

并添加 helper：

```python
def _make_canvas_client():
    return make_client_from_config()


def _canvas_error_payload(exc: CanvasError) -> dict:
    payload = exc.to_dict()
    if exc.code in ("missing_token", "invalid_token"):
        base = dc.load_config().get("canvas_base_url", _CANVAS_DEFAULT_BASE_URL)
        payload["settings_url"] = _canvas_settings_url(base)
        payload["next_action"] = "请先调用 setup_canvas 获取或刷新 Canvas Token。"
    return payload


def _resolve_canvas_course_or_error(client, course) -> dict:
    resolved = client.resolve_course(course)
    if not resolved.get("ok"):
        return resolved
    return {"ok": True, "course": resolved["course"]}
```

- [ ] **Step 2: 在 `TOOLS` 加入五个 tool definitions**

把下列 entries 放在 `list_canvas_assignments` 之前：

```python
    {
        "type": "function",
        "function": {
            "name": "list_canvas_courses",
            "description": "列出当前 Canvas active 课程，可选包含 tabs 和教师信息。用户问 Canvas 有哪些课程、课程 ID、课程代码时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_tabs": {"type": "boolean", "description": "是否包含课程 tabs，默认 false"},
                    "include_teachers": {"type": "boolean", "description": "是否包含教师列表，默认 false"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas_course_announcements",
            "description": "按 Canvas 课程名、课程代码或 course_id 查看某门课公告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course": {"type": "string", "description": "课程名、课程代码或 Canvas course_id"},
                    "limit": {"type": "integer", "description": "最多返回公告数，默认 20"},
                    "since_days": {"type": "integer", "description": "只看最近多少天，可不传"},
                },
                "required": ["course"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas_course_quizzes",
            "description": "按 Canvas 课程名、课程代码或 course_id 查看某门课 quiz/测验。优先 Classic Quizzes，并补充 quiz-backed assignments。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course": {"type": "string", "description": "课程名、课程代码或 Canvas course_id"},
                    "include_past": {"type": "boolean", "description": "是否包含已过期 quiz，默认 true"},
                    "include_assignment_backed": {"type": "boolean", "description": "是否从 assignments 补充识别 quiz，默认 true"},
                },
                "required": ["course"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas_course_updates",
            "description": "聚合查看某门 Canvas 课程的公告、quiz、作业和 activity stream。",
            "parameters": {
                "type": "object",
                "properties": {
                    "course": {"type": "string", "description": "课程名、课程代码或 Canvas course_id"},
                    "include": {"type": "array", "items": {"type": "string"}, "description": "要包含的 sections，默认 announcements/quizzes/assignments/activity"},
                    "limit": {"type": "integer", "description": "每类最多返回数量，默认 10"},
                },
                "required": ["course"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas_todo",
            "description": "查看 Canvas 全局 todo 和 planner items，用于回答近期待办。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "最多返回数量，默认 20"},
                },
                "required": [],
            },
        },
    },
```

- [ ] **Step 3: 在 `_core.py` 添加 tool 函数**

放在 `tool_list_canvas_assignments` 前：

```python
def tool_list_canvas_courses(include_tabs: bool = False, include_teachers: bool = False) -> dict:
    try:
        client = _make_canvas_client()
        return client.list_courses(include_tabs=include_tabs, include_teachers=include_teachers)
    except CanvasError as exc:
        return _canvas_error_payload(exc)


def tool_get_canvas_course_announcements(course, limit: int = 20, since_days: int | None = None) -> dict:
    try:
        client = _make_canvas_client()
        resolved = _resolve_canvas_course_or_error(client, course)
        if not resolved.get("ok"):
            return resolved
        course_info = resolved["course"]
        result = client.list_announcements(course_info["course_id"], limit=limit, since_days=since_days)
        result["course"] = course_info
        return result
    except CanvasError as exc:
        return _canvas_error_payload(exc)


def tool_get_canvas_course_quizzes(
    course,
    include_past: bool = True,
    include_assignment_backed: bool = True,
) -> dict:
    try:
        client = _make_canvas_client()
        resolved = _resolve_canvas_course_or_error(client, course)
        if not resolved.get("ok"):
            return resolved
        course_info = resolved["course"]
        result = client.list_quizzes(
            course_info["course_id"],
            include_past=include_past,
            include_assignment_backed=include_assignment_backed,
        )
        result["course"] = course_info
        return result
    except CanvasError as exc:
        return _canvas_error_payload(exc)


def tool_get_canvas_course_updates(course, include: list[str] | None = None, limit: int = 10) -> dict:
    try:
        client = _make_canvas_client()
        resolved = _resolve_canvas_course_or_error(client, course)
        if not resolved.get("ok"):
            return resolved
        course_info = resolved["course"]
        result = client.get_course_updates(course_info["course_id"], include=include, limit=limit)
        result["course"] = course_info
        return result
    except CanvasError as exc:
        return _canvas_error_payload(exc)


def tool_get_canvas_todo(limit: int = 20) -> dict:
    try:
        client = _make_canvas_client()
        return client.list_todo(limit=limit)
    except CanvasError as exc:
        return _canvas_error_payload(exc)
```

- [ ] **Step 4: 在 `run_tool` 加 dispatch**

在 `list_canvas_assignments` 前加入：

```python
        elif name == "list_canvas_courses":      r = tool_list_canvas_courses(**args)
        elif name == "get_canvas_course_announcements": r = tool_get_canvas_course_announcements(**args)
        elif name == "get_canvas_course_quizzes": r = tool_get_canvas_course_quizzes(**args)
        elif name == "get_canvas_course_updates": r = tool_get_canvas_course_updates(**args)
        elif name == "get_canvas_todo":          r = tool_get_canvas_todo(**args)
```

- [ ] **Step 5: 在 `sjtu_agent/agent/__init__.py` 导出新工具**

在 import list 中加入：

```python
    tool_list_canvas_courses, tool_get_canvas_course_announcements,
    tool_get_canvas_course_quizzes, tool_get_canvas_course_updates,
    tool_get_canvas_todo,
```

- [ ] **Step 6: 运行 tool 测试**

Run:

```bash
pytest tests/test_canvas_tools.py tests/test_canvas_client.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交 Agent tools**

```bash
git add sjtu_agent/agent/tools/_core.py sjtu_agent/agent/__init__.py tests/test_canvas_tools.py
git commit -m "feat: add canvas course tools"
```

## Task 5: 通知模块测试与实现

**Files:**

- Create: `sjtu_agent/notifications.py`
- Test: `tests/test_canvas_watcher.py`

- [ ] **Step 1: 在 watcher 测试文件中加入通知模块测试**

创建 `tests/test_canvas_watcher.py` 的第一组测试：

```python
from __future__ import annotations

import json
from pathlib import Path

from sjtu_agent.notifications import send_notification


def test_send_notification_skips_unconfigured_channels(monkeypatch):
    calls = []
    monkeypatch.setattr("sjtu_agent.notifications._send_system_notification", lambda title, subtitle, body: calls.append(("system", title, subtitle, body)))
    monkeypatch.setattr("sjtu_agent.notifications._send_telegram_notification", lambda cfg, title, subtitle, body: calls.append(("telegram", title)))
    monkeypatch.setattr("sjtu_agent.notifications._send_feishu_notification", lambda cfg, title, subtitle, body: calls.append(("feishu", title)))

    result = send_notification(
        {"telegram_token": "", "telegram_allowed_ids": [], "feishu_app_id": ""},
        "Title",
        "Sub",
        "Body",
        channels=["system", "telegram", "feishu"],
        test_mode=False,
    )

    assert result["ok"] is True
    assert [item["channel"] for item in result["sent"]] == ["system"]
    assert calls == [("system", "Title", "Sub", "Body")]


def test_send_notification_test_mode_does_not_send(monkeypatch):
    monkeypatch.setattr("sjtu_agent.notifications._send_system_notification", lambda *args: (_ for _ in ()).throw(AssertionError("should not send")))

    result = send_notification({}, "Title", "Sub", "Body", channels=["system"], test_mode=True)

    assert result["ok"] is True
    assert result["would_send"][0]["channel"] == "system"
```

- [ ] **Step 2: 运行通知测试确认失败**

Run:

```bash
pytest tests/test_canvas_watcher.py -q
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'sjtu_agent.notifications'`。

- [ ] **Step 3: 实现 `sjtu_agent/notifications.py`**

创建模块：

```python
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request


def _send_system_notification(title: str, subtitle: str, body: str) -> None:
    message = f"{subtitle}\n{body}" if body else subtitle
    try:
        from plyer import notification as _plyer_notif  # type: ignore
        _plyer_notif.notify(title=title, message=message, app_name="SJTU Agent", timeout=10)
        return
    except Exception:
        pass

    if sys.platform == "darwin":
        def esc(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{esc(body)}" with title "{esc(title)}" subtitle "{esc(subtitle)}"'
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=5)
    elif sys.platform == "win32":
        subprocess.run(["powershell", "-Command", f"Write-Host {json.dumps(message)}"], capture_output=True, timeout=10)
    else:
        subprocess.run(["notify-send", title, message], check=True, capture_output=True, timeout=5)


def _send_telegram_notification(cfg: dict, title: str, subtitle: str, body: str) -> None:
    token = cfg.get("telegram_token", "")
    allowed_ids = [int(x) for x in cfg.get("telegram_allowed_ids", [])]
    text = f"🔔 <b>{title}</b>\n<i>{subtitle}</i>"
    if body:
        text += f"\n{body}"
    for uid in allowed_ids:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": uid, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)


def _send_feishu_notification(cfg: dict, title: str, subtitle: str, body: str) -> None:
    import requests
    app_id = cfg.get("feishu_app_id", "")
    app_secret = cfg.get("feishu_app_secret", "")
    open_id = cfg.get("feishu_open_id", "")
    token_resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    token_resp.raise_for_status()
    token_payload = token_resp.json()
    if token_payload.get("code") != 0:
        raise RuntimeError("飞书 tenant_access_token 获取失败")
    tenant_token = token_payload["tenant_access_token"]
    text = f"🔔 {title}\n{subtitle}"
    if body:
        text += f"\n{body}"
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "open_id"},
        headers={"Authorization": f"Bearer {tenant_token}"},
        json={"receive_id": open_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("msg") or "飞书推送失败")


def _channel_configured(cfg: dict, channel: str) -> bool:
    if channel == "system":
        return True
    if channel == "telegram":
        return bool(cfg.get("telegram_enabled", True) and cfg.get("telegram_token") and cfg.get("telegram_allowed_ids"))
    if channel == "feishu":
        return bool(cfg.get("feishu_enabled", True) and cfg.get("feishu_app_id") and cfg.get("feishu_app_secret") and cfg.get("feishu_open_id"))
    if channel == "wechat":
        return bool(cfg.get("wechat_enabled", True))
    return False


def send_notification(
    cfg: dict,
    title: str,
    subtitle: str,
    body: str,
    *,
    channels: list[str] | None = None,
    test_mode: bool = False,
) -> dict:
    channels = channels or ["system", "telegram", "feishu"]
    sent: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    would_send: list[dict] = []

    for channel in channels:
        if not _channel_configured(cfg, channel):
            skipped.append({"channel": channel, "reason": "unconfigured"})
            continue
        if test_mode:
            would_send.append({"channel": channel, "title": title, "subtitle": subtitle, "body": body})
            continue
        try:
            if channel == "system":
                _send_system_notification(title, subtitle, body)
            elif channel == "telegram":
                _send_telegram_notification(cfg, title, subtitle, body)
            elif channel == "feishu":
                _send_feishu_notification(cfg, title, subtitle, body)
            elif channel == "wechat":
                from scripts.wechat_bot import send_reminder_via_wechat
                send_reminder_via_wechat(title, subtitle, body)
            else:
                skipped.append({"channel": channel, "reason": "unsupported"})
                continue
            sent.append({"channel": channel})
        except Exception as exc:
            failed.append({"channel": channel, "error": str(exc)})
    return {"ok": not failed, "sent": sent, "skipped": skipped, "failed": failed, "would_send": would_send}
```

- [ ] **Step 4: 运行通知测试**

Run:

```bash
pytest tests/test_canvas_watcher.py -q
```

Expected: PASS 当前两条测试。

- [ ] **Step 5: 提交通知模块**

```bash
git add sjtu_agent/notifications.py tests/test_canvas_watcher.py
git commit -m "feat: add shared notification helper"
```

## Task 6: Canvas Watcher 测试

**Files:**

- Modify: `tests/test_canvas_watcher.py`

- [ ] **Step 1: 给 watcher 行为添加失败测试**

在 `tests/test_canvas_watcher.py` 追加：

```python
from scripts import canvas_watcher


class FakeWatcherClient:
    def __init__(self, announcements=None, quizzes=None, assignments=None):
        self._courses = [{"course_id": 1, "name": "Signals", "course_code": "ECE2300"}]
        self._announcements = announcements or []
        self._quizzes = quizzes or []
        self._assignments = assignments or []

    def list_courses(self):
        return {"ok": True, "count": len(self._courses), "courses": self._courses}

    def list_announcements(self, course_id, limit=50, since_days=None):
        return {"ok": True, "count": len(self._announcements), "announcements": self._announcements}

    def list_quizzes(self, course_id, include_past=True, include_assignment_backed=True):
        return {"ok": True, "quiz_status": "enabled", "count": len(self._quizzes), "quizzes": self._quizzes, "warnings": []}

    def list_assignments(self, course_id, include_past=True):
        return {"ok": True, "count": len(self._assignments), "assignments": self._assignments}


def test_first_run_baselines_without_notifications(tmp_path, monkeypatch):
    state_path = tmp_path / "canvas_state.json"
    sent = []
    client = FakeWatcherClient(announcements=[{"id": 10, "title": "Exam", "summary": "Read", "posted_at": "2026-06-01", "html_url": "u"}])
    monkeypatch.setattr(canvas_watcher, "send_notification", lambda *args, **kwargs: sent.append((args, kwargs)))

    result = canvas_watcher.check_once(
        cfg={"canvas_monitor": {"baseline_on_first_run": True, "notify_channels": ["system"]}},
        client=client,
        state_path=state_path,
        test_mode=False,
    )

    assert result["ok"] is True
    assert result["baseline_created"] is True
    assert sent == []
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert "announcement:1:10" in saved["items"]


def test_new_announcement_after_baseline_notifies(tmp_path, monkeypatch):
    state_path = tmp_path / "canvas_state.json"
    state_path.write_text(json.dumps({"items": {}, "last_checked_at": "2026-06-01T00:00:00+08:00"}), encoding="utf-8")
    sent = []
    client = FakeWatcherClient(announcements=[{"id": 10, "title": "Exam", "summary": "Read", "posted_at": "2026-06-01", "html_url": "u"}])
    monkeypatch.setattr(canvas_watcher, "send_notification", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True, "sent": [{"channel": "system"}]})

    result = canvas_watcher.check_once(
        cfg={"canvas_monitor": {"baseline_on_first_run": True, "notify_channels": ["system"]}},
        client=client,
        state_path=state_path,
        test_mode=False,
    )

    assert result["events_count"] == 1
    assert sent
    assert sent[0][0][1] == "Canvas 新公告"


def test_quiz_due_change_notifies(tmp_path, monkeypatch):
    state_path = tmp_path / "canvas_state.json"
    state_path.write_text(json.dumps({
        "items": {
            "quiz:1:5": {
                "signature": {"title": "Quiz", "due_at": "old", "unlock_at": None, "lock_at": None, "published": True, "locked_for_user": False, "question_count": 1, "points_possible": 10}
            }
        },
        "last_checked_at": "2026-06-01T00:00:00+08:00",
    }), encoding="utf-8")
    sent = []
    client = FakeWatcherClient(quizzes=[{"quiz_id": 5, "title": "Quiz", "due_at": "new", "unlock_at": None, "lock_at": None, "published": True, "locked_for_user": False, "question_count": 1, "points_possible": 10, "html_url": "u"}])
    monkeypatch.setattr(canvas_watcher, "send_notification", lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True})

    result = canvas_watcher.check_once(
        cfg={"canvas_monitor": {"baseline_on_first_run": True, "notify_channels": ["system"]}},
        client=client,
        state_path=state_path,
        test_mode=False,
    )

    assert result["events_count"] == 1
    assert "due_at" in result["events"][0]["changed_fields"]
    assert sent[0][0][1] == "Canvas Quiz 时间变化"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/test_canvas_watcher.py -q
```

Expected: FAIL，错误包含无法导入 `scripts.canvas_watcher`。

- [ ] **Step 3: 提交 watcher 失败测试**

```bash
git add tests/test_canvas_watcher.py
git commit -m "test: cover canvas watcher state changes"
```

## Task 7: Canvas Watcher 实现

**Files:**

- Create: `scripts/canvas_watcher.py`
- Modify: `sjtu_agent/paths.py`
- Test: `tests/test_canvas_watcher.py`

- [ ] **Step 1: 在 `paths.py` 增加状态路径**

在其他状态路径旁加入：

```python
CANVAS_MONITOR_STATE_PATH = DATA_DIR / "canvas_monitor_state.json"
```

并在 `describe_runtime_paths()` 返回值里加入：

```python
        "canvas_monitor_state_path": str(CANVAS_MONITOR_STATE_PATH),
```

- [ ] **Step 2: 实现 `scripts/canvas_watcher.py`**

创建脚本，至少包含 `check_once`、事件构造、状态读写和 CLI 入口：

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sjtu_agent.canvas_client import CanvasError, make_client_from_config
from sjtu_agent.notifications import send_notification
from sjtu_agent.paths import CANVAS_MONITOR_STATE_PATH, CONFIG_PATH, LOG_DIR, atomic_write_json, read_json_safe

CST = timezone(timedelta(hours=8))
DEFAULT_MONITOR_CFG = {
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


def _log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line)
    try:
        with (LOG_DIR / "canvas_watcher.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _load_cfg() -> dict:
    return read_json_safe(CONFIG_PATH, default={})


def _monitor_cfg(cfg: dict) -> dict:
    merged = dict(DEFAULT_MONITOR_CFG)
    merged.update((cfg.get("canvas_monitor") or {}))
    return merged


def _load_state(path: Path) -> dict:
    state = read_json_safe(path, default={})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("items", {})
    return state


def _save_state(path: Path, state: dict) -> None:
    state["last_checked_at"] = datetime.now(CST).isoformat()
    atomic_write_json(path, state)


def _select_courses(client, monitor: dict) -> list[dict]:
    courses = client.list_courses().get("courses", [])
    ids = {int(x) for x in monitor.get("course_ids") or []}
    filters = [str(x).lower() for x in monitor.get("course_filters") or [] if str(x).strip()]
    if ids:
        return [c for c in courses if int(c["course_id"]) in ids]
    if filters:
        return [
            c for c in courses
            if any(f in str(c.get("name", "")).lower() or f in str(c.get("course_code", "")).lower() for f in filters)
        ]
    return courses


def _announcement_signature(item: dict) -> dict:
    return {
        "title": item.get("title"),
        "posted_at": item.get("posted_at"),
        "updated_at": item.get("updated_at"),
        "message_hash": item.get("message_hash"),
    }


def _quiz_signature(item: dict) -> dict:
    return {
        "title": item.get("title"),
        "unlock_at": item.get("unlock_at"),
        "due_at": item.get("due_at"),
        "lock_at": item.get("lock_at"),
        "published": item.get("published"),
        "locked_for_user": item.get("locked_for_user"),
        "question_count": item.get("question_count"),
        "points_possible": item.get("points_possible"),
    }


def _assignment_signature(item: dict) -> dict:
    return {
        "name": item.get("name"),
        "unlock_at": item.get("unlock_at"),
        "due_at": item.get("due_at"),
        "lock_at": item.get("lock_at"),
        "published": item.get("published"),
        "submission_types": item.get("submission_types"),
    }


def _changed_fields(old: dict, new: dict) -> list[str]:
    return [key for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key)]


def _record_event(state: dict, key: str, signature: dict, event: dict, baseline: bool, events: list[dict]) -> None:
    existing = state["items"].get(key)
    if existing is None:
        state["items"][key] = {"signature": signature}
        if not baseline:
            events.append(event)
        return
    old_sig = existing.get("signature") or {}
    changed = _changed_fields(old_sig, signature)
    if changed:
        state["items"][key] = {"signature": signature}
        event["changed_fields"] = changed
        event["old_signature"] = old_sig
        event["new_signature"] = signature
        events.append(event)


def _collect_events(client, courses: list[dict], monitor: dict, state: dict, baseline: bool) -> list[dict]:
    events: list[dict] = []
    for course in courses:
        cid = int(course["course_id"])
        course_name = course.get("name", f"课程{cid}")
        if monitor.get("include_announcements", True):
            for item in client.list_announcements(cid, limit=50).get("announcements", []):
                key = f"announcement:{cid}:{item.get('id')}"
                _record_event(state, key, _announcement_signature(item), {
                    "type": "new_announcement",
                    "course": course,
                    "item": item,
                    "title": "Canvas 新公告",
                    "subtitle": course_name,
                    "body": f"{item.get('title', '')}\n{item.get('summary', '')}\n{item.get('html_url', '')}",
                }, baseline, events)
        if monitor.get("include_quizzes", True):
            for item in client.list_quizzes(cid).get("quizzes", []):
                item_id = item.get("quiz_id") or item.get("assignment_id")
                key = f"quiz:{cid}:{item_id}"
                event_type = "new_quiz"
                title = "Canvas 新 Quiz"
                if key in state.get("items", {}):
                    event_type = "quiz_changed"
                    title = "Canvas Quiz 时间变化"
                _record_event(state, key, _quiz_signature(item), {
                    "type": event_type,
                    "course": course,
                    "item": item,
                    "title": title,
                    "subtitle": course_name,
                    "body": f"{item.get('title', '')}\ndue: {item.get('due_at')}\nunlock: {item.get('unlock_at')}\nlock: {item.get('lock_at')}\n{item.get('html_url', '')}",
                }, baseline, events)
        if monitor.get("include_assignments", False):
            for item in client.list_assignments(cid).get("assignments", []):
                key = f"assignment:{cid}:{item.get('assignment_id')}"
                _record_event(state, key, _assignment_signature(item), {
                    "type": "new_assignment",
                    "course": course,
                    "item": item,
                    "title": "Canvas 新作业",
                    "subtitle": course_name,
                    "body": f"{item.get('name', '')}\ndue: {item.get('due_at')}\n{item.get('html_url', '')}",
                }, baseline, events)
    return events


def check_once(
    *,
    cfg: dict | None = None,
    client=None,
    state_path: Path = CANVAS_MONITOR_STATE_PATH,
    test_mode: bool = False,
) -> dict:
    cfg = cfg or _load_cfg()
    monitor = _monitor_cfg(cfg)
    if not monitor.get("enabled", True):
        return {"ok": True, "enabled": False, "events_count": 0, "events": []}
    client = client or make_client_from_config()
    state = _load_state(state_path)
    baseline = bool(monitor.get("baseline_on_first_run", True) and not state.get("last_checked_at") and not state.get("items"))
    courses = _select_courses(client, monitor)
    events = _collect_events(client, courses, monitor, state, baseline)
    notification_results = []
    for event in events:
        notification_results.append(send_notification(
            cfg,
            event["title"],
            event["subtitle"],
            event["body"],
            channels=monitor.get("notify_channels") or ["system", "telegram", "feishu"],
            test_mode=test_mode,
        ))
    _save_state(state_path, state)
    return {
        "ok": True,
        "enabled": True,
        "baseline_created": baseline,
        "courses_count": len(courses),
        "events_count": len(events),
        "events": events,
        "notifications": notification_results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor Canvas course updates.")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--test", action="store_true", help="print would-send notifications only")
    args = parser.parse_args(argv)
    while True:
        try:
            result = check_once(test_mode=args.test)
            _log(json.dumps({"events_count": result.get("events_count"), "baseline": result.get("baseline_created")}, ensure_ascii=False))
        except CanvasError as exc:
            _log(f"Canvas watcher skipped: {exc.message}")
        except Exception as exc:
            _log(f"Canvas watcher error: {exc}")
        if args.once:
            return 0
        cfg = _load_cfg()
        interval = int(_monitor_cfg(cfg).get("interval_seconds", 300))
        time.sleep(max(30, interval))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: 运行 watcher 测试**

Run:

```bash
pytest tests/test_canvas_watcher.py -q
```

Expected: PASS。

- [ ] **Step 4: 提交 watcher**

```bash
git add sjtu_agent/paths.py scripts/canvas_watcher.py tests/test_canvas_watcher.py
git commit -m "feat: add canvas watcher"
```

## Task 8: CLI 和 Scheduler 注册

**Files:**

- Modify: `sjtu_agent/cli.py`
- Modify: `sjtu_agent/scheduler/__init__.py`
- Modify: `sjtu_agent/scheduler/launchd.py`
- Modify: `sjtu_agent/scheduler/taskschd.py`
- Modify: `sjtu_agent/scheduler/systemd.py`
- Modify: `sjtu_agent/scheduler/psmuxd.py`

- [ ] **Step 1: 在 CLI 注册 `canvas-watcher`**

在 `sjtu_agent/cli.py` 中新增：

```python
def _cmd_canvas_watcher(args: argparse.Namespace) -> int:
    return _run_script("canvas_watcher", args.script_args)
```

并在 parser 注册区加入：

```python
    _add_passthrough_parser(subparsers, "canvas-watcher", "monitor Canvas course announcements and quizzes", _cmd_canvas_watcher)
```

- [ ] **Step 2: 在 scheduler service list 加入 `canvas-watcher`**

在 `sjtu_agent/scheduler/__init__.py` 的 `available_service_names()` 返回 tuple 里加入 `"canvas-watcher"`，位置放在 `"email-watcher"` 后。

- [ ] **Step 3: macOS launchd specs**

在 `sjtu_agent/scheduler/launchd.py` 的 `_SERVICE_SPECS` 加入：

```python
    "canvas-watcher": {
        "label": "com.sjtu.canvas-watcher",
        "subcommand": "canvas-watcher --once",
        "log": "canvas_watcher.launchd.log",
        "run_at_load": True,
        "schedule_type": "interval",
        "keep_alive": False,
    },
```

- [ ] **Step 4: Windows Task Scheduler specs**

在 `sjtu_agent/scheduler/taskschd.py` 的 `_SERVICE_SPECS` 加入：

```python
    "canvas-watcher": {
        "task_name": f"{_TASK_PREFIX}-CanvasWatcher",
        "subcommand": "canvas-watcher --once",
        "log": "canvas_watcher.task.log",
        "schedule": "minute",
    },
```

- [ ] **Step 5: Linux systemd specs**

在 `sjtu_agent/scheduler/systemd.py` 的 `_SERVICE_SPECS` 加入：

```python
    "canvas-watcher": {
        "unit_name": "sjtu-agent-canvas-watcher",
        "subcommand": "canvas-watcher --once",
        "log": "canvas_watcher.systemd.log",
        "restart": "no",
        "has_timer": True,
        "timer_type": "interval",
    },
```

- [ ] **Step 6: Windows psmux specs**

在 `sjtu_agent/scheduler/psmuxd.py` 的 `_SERVICE_SPECS` 加入：

```python
    "canvas-watcher": {
        "session_name": "canvas-watcher",
        "subcommand": "canvas-watcher",
    },
```

- [ ] **Step 7: 运行 CLI smoke 和相关测试**

Run:

```bash
python3 -m sjtu_agent --help >/tmp/sjtu_agent_help.txt
python3 -m sjtu_agent canvas-watcher --help >/tmp/sjtu_agent_canvas_help.txt
pytest tests/test_canvas_client.py tests/test_canvas_tools.py tests/test_canvas_watcher.py -q
```

Expected: help commands exit 0，pytest PASS。

- [ ] **Step 8: 提交 CLI/scheduler 注册**

```bash
git add sjtu_agent/cli.py sjtu_agent/scheduler/__init__.py sjtu_agent/scheduler/launchd.py sjtu_agent/scheduler/taskschd.py sjtu_agent/scheduler/systemd.py sjtu_agent/scheduler/psmuxd.py
git commit -m "feat: register canvas watcher service"
```

## Task 9: 文档更新与全量验证

**Files:**

- Modify: `README.md`
- Modify: `README_EN.md`

- [ ] **Step 1: 更新中文 README**

在 README 的 CLI/后台服务/Canvas 配置相关区域补充：

```markdown
### Canvas 课程查询与监控

Agent 现在可以按课程查询 Canvas 内容：

- “列出我的 Canvas 课程”
- “查看 ECE2300 的公告”
- “查看 MATH2030 的 quiz”
- “汇总 TC3000 这门课最近更新”
- “Canvas 最近有什么待办”

后台监控命令：

```bash
sjtu-agent canvas-watcher --once
sjtu-agent canvas-watcher --once --test
sjtu-agent install-daemons --services canvas-watcher
```

可选配置写在运行时 `config.json`：

```json
{
  "canvas_monitor": {
    "enabled": true,
    "course_ids": [],
    "course_filters": [],
    "include_announcements": true,
    "include_quizzes": true,
    "include_assignments": false,
    "include_activity": false,
    "interval_seconds": 300,
    "notify_channels": ["system", "telegram", "feishu"],
    "baseline_on_first_run": true
  }
}
```

说明：部分课程会禁用 Canvas 的 Quizzes、Pages 等 tabs，这是课程设置导致的正常状态。当前 SJTU Canvas 主要可依赖 Classic Quizzes API；New Quizzes API 暂不作为主路径。
```

- [ ] **Step 2: 更新英文 README**

在 README_EN 的对应区域补充英文说明：

```markdown
### Canvas Course Queries and Monitoring

The Agent can query Canvas content by course:

- "List my Canvas courses"
- "Show announcements for ECE2300"
- "Show quizzes for MATH2030"
- "Summarize recent updates for TC3000"
- "What Canvas todos do I have?"

Watcher commands:

```bash
sjtu-agent canvas-watcher --once
sjtu-agent canvas-watcher --once --test
sjtu-agent install-daemons --services canvas-watcher
```

Optional runtime `config.json` block:

```json
{
  "canvas_monitor": {
    "enabled": true,
    "course_ids": [],
    "course_filters": [],
    "include_announcements": true,
    "include_quizzes": true,
    "include_assignments": false,
    "include_activity": false,
    "interval_seconds": 300,
    "notify_channels": ["system", "telegram", "feishu"],
    "baseline_on_first_run": true
  }
}
```

Some courses disable Canvas tabs such as Quizzes or Pages; this is a normal course-level setting. SJTU Canvas currently appears to expose Classic Quizzes reliably, while New Quizzes API should not be treated as the primary path.
```

- [ ] **Step 3: 运行全量测试**

Run:

```bash
pytest -q
```

Expected: PASS。

- [ ] **Step 4: 手动本地只读验证**

Run:

```bash
python3 - <<'PY'
from sjtu_agent.agent import tools
print(tools.tool_list_canvas_courses()["count"])
print(tools.tool_get_canvas_course_quizzes("ECE2300").get("quiz_status"))
PY
python3 -m sjtu_agent canvas-watcher --once --test
```

Expected: 第一段打印课程数量和 quiz 状态；第二段不真实发送通知，退出码 0。

- [ ] **Step 5: 提交文档和最终验证**

```bash
git add README.md README_EN.md
git commit -m "docs: document canvas course monitoring"
```

- [ ] **Step 6: 最终状态检查**

Run:

```bash
git status --short
git log --oneline -8
```

Expected: 工作区干净，最近提交包含本计划中的任务提交。
