from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any

import requests

from sjtu_agent.paths import CONFIG_PATH, DATA_DIR, atomic_write_json, read_json_safe

DEFAULT_CANVAS_BASE_URL = "https://oc.sjtu.edu.cn"
CANVAS_COURSES_CACHE_PATH = DATA_DIR / "canvas_courses_cache.json"
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


def _course_lookup_text(course: dict) -> str:
    return " ".join(
        str(course.get(key, "") or "")
        for key in ("name", "course_code")
    ).lower()


def _course_number_tokens(text: str) -> list[str]:
    tokens = re.findall(r"\d{3,6}", text or "")
    unique: list[str] = []
    for token in tokens:
        if token not in unique:
            unique.append(token)
    return unique


def _slim_courses_for_cache(courses: list[dict]) -> list[dict]:
    slim_courses = []
    for course in courses:
        if not isinstance(course, dict):
            continue
        slim_courses.append({
            "course_id": course.get("course_id"),
            "name": course.get("name", ""),
            "course_code": course.get("course_code", ""),
            "workflow_state": course.get("workflow_state", ""),
            "default_view": course.get("default_view", ""),
        })
    return slim_courses


def _save_course_cache(courses: list[dict]) -> None:
    try:
        atomic_write_json(CANVAS_COURSES_CACHE_PATH, {
            "courses": _slim_courses_for_cache(courses),
            "updated_at": datetime.now(CST).isoformat(),
        })
    except Exception:
        pass


def _load_course_cache() -> list[dict]:
    data = read_json_safe(CANVAS_COURSES_CACHE_PATH, default={})
    courses = data.get("courses", []) if isinstance(data, dict) else []
    if not isinstance(courses, list):
        return []
    result = []
    for course in courses:
        if not isinstance(course, dict) or not course.get("course_id"):
            continue
        cached = dict(course)
        cached["from_cache"] = True
        result.append(cached)
    return result


def _match_courses_by_number(courses: list[dict], text: str) -> tuple[str, list[dict]]:
    groups: list[tuple[int, int, int, str, list[dict]]] = []
    for order, token in enumerate(_course_number_tokens(text)):
        matches = []
        for course in courses:
            lookup = _course_lookup_text(course)
            digits = "".join(re.findall(r"\d+", lookup))
            course_id = str(course.get("course_id", ""))
            if token == course_id or token in digits:
                matches.append(course)
        if matches:
            groups.append((len(matches), -len(token), order, token, matches))
    if not groups:
        return "", []
    _, _, _, token, matches = sorted(groups, key=lambda item: item[:4])[0]
    return token, matches


class CanvasClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        session: requests.Session | None = None,
        timeout: int = 15,
    ):
        self.base_url = (base_url or DEFAULT_CANVAS_BASE_URL).rstrip("/")
        self.token = (token or "").strip()
        self.timeout = timeout
        if not self.token or self.token.startswith("YOUR_"):
            raise CanvasError("missing_token", "未配置 Canvas Token，请先运行 setup_canvas。")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        })

    def _get_json(self, path: str, params: dict | None = None) -> tuple[object, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                params=params or {},
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise CanvasError("timeout", f"Canvas 请求超时: {path}") from exc
        except requests.RequestException as exc:
            raise CanvasError("request_error", f"Canvas 请求失败: {exc}") from exc

        try:
            payload = response.json()
        except Exception as exc:
            raise CanvasError(
                "invalid_json",
                f"Canvas 返回不是 JSON: {path}",
                status_code=response.status_code,
            ) from exc

        if response.status_code in (401, 403):
            raise CanvasError(
                "invalid_token",
                "Canvas Token 无效或权限不足，请重新运行 setup_canvas。",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, dict) else str(payload)
            raise CanvasError(
                "http_error",
                str(message or "Canvas 请求失败"),
                status_code=response.status_code,
            )
        return payload, response

    def _get_list_page(self, path: str, params: dict | None = None) -> tuple[list, Any]:
        payload, response = self._get_json(path, params=params)
        if not isinstance(payload, list):
            raise CanvasError(
                "unexpected_schema",
                f"Canvas list endpoint returned {type(payload).__name__}: {path}",
            )
        return payload, response

    def _get_all_pages(self, path: str, params: dict | None = None, max_pages: int = 20) -> list:
        items: list = []
        next_url: str | None = f"{self.base_url}{path}"
        current_params = params or {}
        pages = 0
        while next_url and pages < max_pages:
            response = None
            payload = None
            last_json_error: Exception | None = None
            for attempt in range(3):
                try:
                    response = self.session.get(next_url, params=current_params, timeout=self.timeout)
                except requests.Timeout as exc:
                    if attempt < 2:
                        time.sleep(0.3 * (attempt + 1))
                        continue
                    raise CanvasError("timeout", f"Canvas 请求超时: {path}") from exc
                except requests.RequestException as exc:
                    if attempt < 2:
                        time.sleep(0.3 * (attempt + 1))
                        continue
                    raise CanvasError("request_error", f"Canvas 请求失败: {exc}") from exc
                if response.status_code in (502, 503, 504) and attempt < 2:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                try:
                    payload = response.json()
                    break
                except Exception as exc:
                    last_json_error = exc
                    if response.status_code in (502, 503, 504) and attempt < 2:
                        time.sleep(0.3 * (attempt + 1))
                        continue
                    raise CanvasError(
                        "invalid_json",
                        f"Canvas 返回不是 JSON: {path}",
                        status_code=response.status_code,
                    ) from exc
            if response is None:
                raise CanvasError("request_error", f"Canvas 请求失败: {path}")
            if payload is None:
                raise CanvasError(
                    "invalid_json",
                    f"Canvas 返回不是 JSON: {path}",
                    status_code=response.status_code,
                ) from last_json_error
            if response.status_code in (401, 403):
                raise CanvasError(
                    "invalid_token",
                    "Canvas Token 无效或权限不足，请重新运行 setup_canvas。",
                    status_code=response.status_code,
                )
            if response.status_code >= 400:
                message = payload.get("message") if isinstance(payload, dict) else str(payload)
                raise CanvasError(
                    "http_error",
                    str(message or "Canvas 请求失败"),
                    status_code=response.status_code,
                )
            if not isinstance(payload, list):
                raise CanvasError(
                    "unexpected_schema",
                    f"Canvas list endpoint returned {type(payload).__name__}: {path}",
                )
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
        courses = [
            self._normalize_course(course)
            for course in raw_courses
            if isinstance(course, dict) and course.get("id")
        ]
        for course in courses:
            course_id = course["course_id"]
            if include_tabs:
                course["tabs"] = self.list_tabs(course_id).get("tabs", [])
            if include_teachers:
                course["teachers"] = self.list_teachers(course_id).get("teachers", [])
        _save_course_cache(courses)
        return {"ok": True, "count": len(courses), "courses": courses}

    def resolve_course(self, query: str | int) -> dict:
        try:
            courses = self.list_courses()["courses"]
        except CanvasError:
            courses = _load_course_cache()
            if not courses:
                raise
        text = str(query).strip()
        if text.isdigit():
            course_id = int(text)
            for course in courses:
                if course["course_id"] == course_id:
                    return {"ok": True, "course": course}

        lowered = text.lower()
        exact = [
            course for course in courses
            if course.get("name") == text
            or str(course.get("course_code", "")).lower() == lowered
        ]
        if len(exact) == 1:
            return {"ok": True, "course": exact[0]}

        matches = [
            course for course in courses
            if lowered in str(course.get("name", "")).lower()
            or lowered in str(course.get("course_code", "")).lower()
        ]
        if len(matches) == 1:
            return {"ok": True, "course": matches[0]}
        if len(matches) > 1:
            return {
                "ok": False,
                "error": "ambiguous_course",
                "query": text,
                "candidates": matches,
            }
        token, number_matches = _match_courses_by_number(courses, text)
        if len(number_matches) == 1:
            return {"ok": True, "course": number_matches[0], "matched_by": f"number:{token}"}
        if len(number_matches) > 1:
            return {
                "ok": False,
                "error": "ambiguous_course",
                "query": text,
                "matched_by": f"number:{token}",
                "candidates": number_matches,
            }
        return {
            "ok": False,
            "error": "course_not_found",
            "query": text,
            "sample_courses": courses[:10],
        }

    def get_course(self, course_id: int) -> dict:
        payload, _ = self._get_json(
            f"/api/v1/courses/{course_id}",
            {"include[]": ["term", "teachers", "syllabus_body"]},
        )
        if not isinstance(payload, dict):
            raise CanvasError("unexpected_schema", "课程详情不是对象")
        return {"ok": True, "course": self._normalize_course(payload)}

    def list_tabs(self, course_id: int) -> dict:
        payload, _ = self._get_list_page(f"/api/v1/courses/{course_id}/tabs")
        tabs = [
            {
                "id": tab.get("id"),
                "label": tab.get("label"),
                "html_url": tab.get("html_url"),
                "type": tab.get("type"),
            }
            for tab in payload
            if isinstance(tab, dict)
        ]
        return {"ok": True, "count": len(tabs), "tabs": tabs}

    def list_teachers(self, course_id: int) -> dict:
        payload, _ = self._get_list_page(
            f"/api/v1/courses/{course_id}/users",
            {"enrollment_type[]": "teacher", "per_page": 100},
        )
        teachers = [
            {
                "id": teacher.get("id"),
                "name": teacher.get("name"),
                "sortable_name": teacher.get("sortable_name"),
            }
            for teacher in payload
            if isinstance(teacher, dict)
        ]
        return {"ok": True, "count": len(teachers), "teachers": teachers}

    def list_announcements(
        self,
        course_id: int,
        limit: int = 20,
        since_days: int | None = None,
    ) -> dict:
        safe_limit = max(1, min(int(limit or 20), 100))
        params = {"context_codes[]": f"course_{course_id}", "per_page": safe_limit}
        payload, _ = self._get_list_page("/api/v1/announcements", params)
        announcements = [
            self._normalize_announcement(item)
            for item in payload
            if isinstance(item, dict)
        ]
        if since_days is not None:
            cutoff = datetime.now(CST) - timedelta(days=max(0, int(since_days)))
            announcements = [
                item for item in announcements
                if _parse_dt(item.get("posted_at")) is None
                or _parse_dt(item.get("posted_at")) >= cutoff
            ]
        return {"ok": True, "count": len(announcements), "announcements": announcements[:safe_limit]}

    def list_assignments(self, course_id: int, include_past: bool = False) -> dict:
        payload = self._get_all_pages(
            f"/api/v1/courses/{course_id}/assignments",
            {"per_page": 100, "order_by": "due_at"},
        )
        assignments = [
            self._normalize_assignment(item)
            for item in payload
            if isinstance(item, dict)
        ]
        if not include_past:
            assignments = [
                item for item in assignments
                if _is_current_canvas_item(item)
            ]
        return {"ok": True, "count": len(assignments), "assignments": assignments}

    def list_quizzes(
        self,
        course_id: int,
        include_past: bool = False,
        include_assignment_backed: bool = True,
    ) -> dict:
        warnings: list[str] = []
        quiz_status = "unknown"
        quizzes: list[dict] = []
        try:
            payload, _ = self._get_list_page(
                f"/api/v1/courses/{course_id}/quizzes",
                {"per_page": 100},
            )
            quiz_status = "enabled"
            quizzes = [
                self._normalize_quiz(item)
                for item in payload
                if isinstance(item, dict)
            ]
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
            quizzes = [
                item for item in quizzes
                if _is_current_canvas_item(item)
            ]
        return {
            "ok": True,
            "quiz_status": quiz_status,
            "count": len(quizzes),
            "quizzes": quizzes,
            "warnings": warnings,
        }

    def list_activity(self, course_id: int, limit: int = 10) -> dict:
        safe_limit = max(1, min(int(limit or 10), 100))
        payload, _ = self._get_list_page(
            f"/api/v1/courses/{course_id}/activity_stream",
            {"per_page": safe_limit},
        )
        items = [
            self._normalize_activity(item)
            for item in payload
            if isinstance(item, dict)
        ]
        return {"ok": True, "count": len(items), "activity": items[:safe_limit]}

    def list_todo(self, limit: int = 20) -> dict:
        safe_limit = max(1, min(int(limit or 20), 100))
        todo, _ = self._get_list_page("/api/v1/users/self/todo")
        planner, _ = self._get_list_page("/api/v1/planner/items", {"per_page": safe_limit})
        items = [
            self._normalize_todo(item)
            for item in todo
            if isinstance(item, dict)
        ]
        items.extend(
            self._normalize_planner_item(item)
            for item in planner
            if isinstance(item, dict)
        )
        return {"ok": True, "count": len(items[:safe_limit]), "items": items[:safe_limit]}

    def list_planner_items(self, limit: int = 20) -> dict:
        safe_limit = max(1, min(int(limit or 20), 100))
        payload, _ = self._get_list_page("/api/v1/planner/items", {"per_page": safe_limit})
        items = [
            self._normalize_planner_item(item)
            for item in payload
            if isinstance(item, dict)
        ]
        return {"ok": True, "count": len(items), "items": items[:safe_limit]}

    def get_course_updates(
        self,
        course_id: int,
        include: list[str] | None = None,
        limit: int = 10,
        include_past: bool = False,
    ) -> dict:
        include = include or ["announcements", "quizzes", "assignments", "activity"]
        sections: dict[str, object] = {}
        warnings: list[str] = []
        for name in include:
            try:
                if name == "announcements":
                    sections[name] = self.list_announcements(course_id, limit=limit)
                elif name == "quizzes":
                    sections[name] = self.list_quizzes(course_id, include_past=include_past)
                elif name == "assignments":
                    sections[name] = self.list_assignments(course_id, include_past=include_past)
                elif name == "activity":
                    sections[name] = self.list_activity(course_id, limit=limit)
            except CanvasError as exc:
                warnings.append(f"{name}: {exc.message}")
        return {
            "ok": True,
            "course_id": course_id,
            "sections": sections,
            "warnings": warnings,
        }

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
        submission_types = item.get("submission_types") or []
        quiz_id = item.get("quiz_id") or item.get("original_quiz_id")
        return {
            "assignment_id": item.get("id"),
            "name": item.get("name", ""),
            "due_at": item.get("due_at"),
            "unlock_at": item.get("unlock_at"),
            "lock_at": item.get("lock_at"),
            "published": item.get("published"),
            "locked_for_user": item.get("locked_for_user"),
            "points_possible": item.get("points_possible"),
            "submission_types": submission_types,
            "quiz_id": quiz_id,
            "is_quiz_assignment": bool(
                item.get("is_quiz_assignment")
                or quiz_id
                or "online_quiz" in submission_types
            ),
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


def _is_current_canvas_item(item: dict) -> bool:
    cutoff = _parse_dt(item.get("lock_at")) or _parse_dt(item.get("due_at"))
    if cutoff is None:
        return True
    return cutoff >= datetime.now(CST)


def _merge_assignment_backed_quizzes(quizzes: list[dict], assignments: list[dict]) -> list[dict]:
    seen_quiz_ids = {item.get("quiz_id") for item in quizzes if item.get("quiz_id")}
    seen_assignment_ids = {
        item.get("assignment_id")
        for item in quizzes
        if item.get("assignment_id")
    }
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
