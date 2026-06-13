from __future__ import annotations

import json

from sjtu_agent.notifications import send_notification


def test_send_notification_skips_unconfigured_channels(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sjtu_agent.notifications._send_system_notification",
        lambda title, subtitle, body: calls.append(("system", title, subtitle, body)),
    )
    monkeypatch.setattr(
        "sjtu_agent.notifications._send_telegram_notification",
        lambda cfg, title, subtitle, body: calls.append(("telegram", title)),
    )
    monkeypatch.setattr(
        "sjtu_agent.notifications._send_feishu_notification",
        lambda cfg, title, subtitle, body: calls.append(("feishu", title)),
    )

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
    monkeypatch.setattr(
        "sjtu_agent.notifications._send_system_notification",
        lambda *args: (_ for _ in ()).throw(AssertionError("should not send")),
    )

    result = send_notification({}, "Title", "Sub", "Body", channels=["system"], test_mode=True)

    assert result["ok"] is True
    assert result["would_send"][0]["channel"] == "system"


from scripts import canvas_watcher


def test_monitor_cfg_uses_shared_defaults_and_clamps_interval():
    monitor = canvas_watcher._monitor_cfg({
        "canvas_monitor": {
            "interval_seconds": 1,
            "notify_channels": ["system", "bad"],
        }
    })

    assert monitor["interval_seconds"] == 30
    assert monitor["notify_channels"] == ["system"]
    assert monitor["include_announcements"] is True
    assert monitor["include_quizzes"] is True


class FakeWatcherClient:
    def __init__(self, announcements=None, quizzes=None, assignments=None):
        self._courses = [{"course_id": 1, "name": "Signals", "course_code": "ECE2300"}]
        self._announcements = announcements or []
        self._quizzes = quizzes or []
        self._assignments = assignments or []
        self.calls = []

    def list_courses(self):
        return {"ok": True, "count": len(self._courses), "courses": self._courses}

    def list_announcements(self, course_id, limit=50, since_days=None):
        return {"ok": True, "count": len(self._announcements), "announcements": self._announcements}

    def list_quizzes(self, course_id, include_past=True, include_assignment_backed=True):
        self.calls.append(("quizzes", course_id, include_past, include_assignment_backed))
        return {"ok": True, "quiz_status": "enabled", "count": len(self._quizzes), "quizzes": self._quizzes, "warnings": []}

    def list_assignments(self, course_id, include_past=True):
        self.calls.append(("assignments", course_id, include_past))
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


def test_watcher_checks_only_current_quizzes_and_assignments_by_default(tmp_path, monkeypatch):
    state_path = tmp_path / "canvas_state.json"
    client = FakeWatcherClient(
        quizzes=[{"quiz_id": 5, "title": "Quiz", "html_url": "u"}],
        assignments=[{"assignment_id": 9, "name": "HW", "html_url": "u"}],
    )
    monkeypatch.setattr(canvas_watcher, "send_notification", lambda *args, **kwargs: {"ok": True})

    result = canvas_watcher.check_once(
        cfg={"canvas_monitor": {"include_assignments": True, "notify_channels": ["system"]}},
        client=client,
        state_path=state_path,
        test_mode=True,
    )

    assert result["ok"] is True
    assert ("quizzes", 1, False, True) in client.calls
    assert ("assignments", 1, False) in client.calls
