from __future__ import annotations

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
