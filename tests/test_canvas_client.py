from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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


def iso_from_now(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    future_due = iso_from_now(7)
    c = client({
        key("/api/v1/courses/92355/quizzes", {"per_page": 100}): FakeResponse(200, [
            {"id": 77327, "assignment_id": 410908, "title": "Quiz 0", "due_at": None, "lock_at": future_due, "html_url": "https://oc/quizzes/77327"}
        ]),
        key("/api/v1/courses/92355/assignments", {"per_page": 100, "order_by": "due_at"}): FakeResponse(200, [
            {"id": 410908, "name": "Quiz 0", "quiz_id": 77327, "submission_types": ["online_quiz"], "due_at": None, "lock_at": future_due, "html_url": "https://oc/assignments/410908"},
            {"id": 410909, "name": "Quiz 1", "submission_types": ["online_quiz"], "due_at": future_due, "html_url": "https://oc/assignments/410909"},
        ]),
    })

    result = c.list_quizzes(92355)

    assert result["quiz_status"] == "enabled"
    assert result["count"] == 2
    assert result["quizzes"][0]["quiz_id"] == 77327
    assert result["quizzes"][1]["source"] == "assignment"
    assert result["quizzes"][1]["assignment_id"] == 410909


def test_quizzes_filter_expired_by_default_and_can_include_past():
    past_lock = iso_from_now(-2)
    future_lock = iso_from_now(2)
    c = client({
        key("/api/v1/courses/92355/quizzes", {"per_page": 100}): FakeResponse(200, [
            {"id": 1, "title": "Past Quiz", "lock_at": past_lock, "html_url": "https://oc/quizzes/1"},
            {"id": 2, "title": "Future Quiz", "lock_at": future_lock, "html_url": "https://oc/quizzes/2"},
            {"id": 3, "title": "No Date Quiz", "lock_at": None, "due_at": None, "unlock_at": None, "html_url": "https://oc/quizzes/3"},
        ]),
        key("/api/v1/courses/92355/assignments", {"per_page": 100, "order_by": "due_at"}): FakeResponse(200, []),
    })

    current = c.list_quizzes(92355)
    with_past = c.list_quizzes(92355, include_past=True)

    assert [item["title"] for item in current["quizzes"]] == ["Future Quiz", "No Date Quiz"]
    assert [item["title"] for item in with_past["quizzes"]] == ["Past Quiz", "Future Quiz", "No Date Quiz"]


def test_assignments_filter_expired_by_default_and_can_include_past():
    past_due = iso_from_now(-1)
    future_due = iso_from_now(3)
    c = client({
        key("/api/v1/courses/92355/assignments", {"per_page": 100, "order_by": "due_at"}): FakeResponse(200, [
            {"id": 1, "name": "Past HW", "due_at": past_due, "html_url": "https://oc/assignments/1"},
            {"id": 2, "name": "Future HW", "due_at": future_due, "html_url": "https://oc/assignments/2"},
            {"id": 3, "name": "No Date HW", "due_at": None, "html_url": "https://oc/assignments/3"},
        ]),
    })

    current = c.list_assignments(92355)
    with_past = c.list_assignments(92355, include_past=True)

    assert [item["name"] for item in current["assignments"]] == ["Future HW", "No Date HW"]
    assert [item["name"] for item in with_past["assignments"]] == ["Past HW", "Future HW", "No Date HW"]


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
