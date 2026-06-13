from __future__ import annotations

import json
import subprocess
import sys
import urllib.request


def _send_system_notification(title: str, subtitle: str, body: str) -> None:
    message = f"{subtitle}\n{body}" if body else subtitle
    try:
        from plyer import notification as _plyer_notif  # type: ignore
        _plyer_notif.notify(
            title=title,
            message=message,
            app_name="SJTU Agent",
            timeout=10,
        )
        return
    except Exception:
        pass

    if sys.platform == "darwin":
        def esc(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')

        script = (
            f'display notification "{esc(body)}" '
            f'with title "{esc(title)}" '
            f'subtitle "{esc(subtitle)}"'
        )
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=5)
    elif sys.platform == "win32":
        subprocess.run(
            ["powershell", "-Command", f"Write-Host {json.dumps(message)}"],
            capture_output=True,
            timeout=10,
        )
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
        json={
            "receive_id": open_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
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
        return bool(
            cfg.get("telegram_enabled", True)
            and cfg.get("telegram_token")
            and cfg.get("telegram_allowed_ids")
        )
    if channel == "feishu":
        return bool(
            cfg.get("feishu_enabled", True)
            and cfg.get("feishu_app_id")
            and cfg.get("feishu_app_secret")
            and cfg.get("feishu_open_id")
        )
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
            would_send.append({
                "channel": channel,
                "title": title,
                "subtitle": subtitle,
                "body": body,
            })
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
    return {
        "ok": not failed,
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "would_send": would_send,
    }
