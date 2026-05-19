#!/usr/bin/env python3
"""
feishu_bot.py — 将 agent.py 接入飞书（Lark）自建应用，长连接接收消息。

用法:
  python3 feishu_bot.py           # 正常运行（WebSocket 长连接）
  python3 feishu_bot.py --test    # 仅校验凭据
  python3 feishu_bot.py --whoami  # 启动 bot 并把每个发送者的 open_id 打到控制台

配置（config.json）:
  feishu_app_id              : 自建应用 App ID（cli_xxx）
  feishu_app_secret          : App Secret
  feishu_allowed_open_ids    : 允许使用的 open_id 列表；留空 [] 时所有人可用
                               （建议先留空，让 bot 把每条来访的 open_id 回显出来再加白名单）

事件订阅: im.message.receive_v1（接收消息 v2.0）
事件接收: 使用长连接（在飞书开放平台「事件与回调」中切换）
"""

import argparse
import io
import json
import re
import sys
import threading
import datetime as _dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sjtu_agent.paths import CONFIG_PATH

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
)

import agent


def _load_cfg() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


cfg = _load_cfg()
APP_ID = cfg.get("feishu_app_id", "").strip()
APP_SECRET = cfg.get("feishu_app_secret", "").strip()
ALLOWED_OPEN_IDS: set[str] = set(cfg.get("feishu_allowed_open_ids", []) or [])

if not APP_ID or not APP_SECRET:
    print("❌ config.json 中未设置 feishu_app_id / feishu_app_secret，请先在 WebUI 或 setup 中配置")
    sys.exit(1)


# ── 全局 API client（用来回复消息） ────────────────────────────────────────────
_api_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

# ── 会话状态（每个 open_id 独立） ─────────────────────────────────────────────
_sessions: dict[str, dict] = {}
_locks: dict[str, threading.Lock] = {}
_sess_meta_lock = threading.Lock()


def _get_session(open_id: str) -> tuple[dict, threading.Lock]:
    with _sess_meta_lock:
        if open_id not in _sessions:
            agent_cfg = agent.load_agent_config()
            _sessions[open_id] = {
                "messages": [],
                "model_box": [agent_cfg["model"]],
                "client_box": [agent._make_client(agent_cfg)],
            }
            _locks[open_id] = threading.Lock()
        return _sessions[open_id], _locks[open_id]


_FS_CTX = (
    "\n\n## 当前运行环境：飞书 Bot\n"
    "你正在通过飞书（Lark）与用户交互：\n"
    "- 回复以纯文本形式发出，飞书会自动渲染基本 markdown。\n"
    "- 不要在回复中给出本地文件路径或让用户在终端操作的指令。\n"
)


def _build_date_ctx() -> str:
    now = _dt.datetime.now()
    year = now.year
    month = now.month
    if month >= 9:
        cur_xnm, cur_xqm = year, "1"
        prev_xnm, prev_xqm = year - 1, "2"
    elif month <= 6:
        cur_xnm, cur_xqm = year - 1, "2"
        prev_xnm, prev_xqm = year - 1, "1"
    else:
        cur_xnm, cur_xqm = year - 1, "3"
        prev_xnm, prev_xqm = year - 1, "2"
    return (
        f"\n\n## 当前时间（每轮自动刷新）\n"
        f"现在：{now.strftime('%Y年%m月%d日 %H:%M')}，星期{'一二三四五六日'[now.weekday()]}。\n"
        f"当前学期：{cur_xnm}-{cur_xnm+1}学年第{cur_xqm}学期。\n"
        f"「上学期」={prev_xnm}-{prev_xnm+1}学年第{prev_xqm}学期"
        f"（query_grades: year='{prev_xnm}', semester='{prev_xqm}'）。\n"
        f"「本学期」={cur_xnm}-{cur_xnm+1}学年第{cur_xqm}学期"
        f"（query_grades: year='{cur_xnm}', semester='{cur_xqm}'）。"
    )


def _init_messages(sess: dict) -> None:
    if sess["messages"]:
        return
    sess["messages"].append({
        "role": "system",
        "content": agent.SYSTEM_PROMPT + _build_date_ctx() + _FS_CTX,
    })


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mKABCDEFGHJKST]")


def _capture_turn(sess: dict, user_text: str) -> str:
    """Run one agent turn, capture its stdout, return the assistant reply text."""
    _init_messages(sess)
    if sess["messages"] and sess["messages"][0]["role"] == "system":
        sess["messages"][0]["content"] = agent.SYSTEM_PROMPT + _build_date_ctx() + _FS_CTX
    sess["messages"].append({"role": "user", "content": user_text})

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        agent._run_one_turn(
            sess["client_box"][0],
            sess["model_box"][0],
            sess["messages"],
        )
    finally:
        sys.stdout = old_stdout

    clean = _ANSI_RE.sub("", buf.getvalue())
    marker = "Agent: "
    idx = clean.rfind(marker)
    if idx == -1:
        for m in reversed(sess["messages"]):
            if m.get("role") == "assistant":
                content = m.get("content", "")
                if isinstance(content, str):
                    return content.strip() or "(已完成)"
                if isinstance(content, list):
                    texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                    return "\n".join(texts).strip() or "(已完成)"
        return "(已完成)"
    return clean[idx + len(marker):].strip()


# ── 回复与拆包 ───────────────────────────────────────────────────────────────

_FS_MSG_MAX = 4000  # 飞书单条文本消息长度上限约 5000，留点余量


def _reply_text(message_id: str, text: str) -> None:
    """回复一条文本消息（线程内回复，飞书 UI 显示成 reply）。"""
    if not text:
        text = "(已完成)"
    chunks = [text[i:i + _FS_MSG_MAX] for i in range(0, len(text), _FS_MSG_MAX)] or [text]
    for chunk in chunks:
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(json.dumps({"text": chunk}, ensure_ascii=False))
                .msg_type("text")
                .build()
            )
            .build()
        )
        resp = _api_client.im.v1.message.reply(req)
        if not resp.success():
            print(f"[feishu] 回复失败 code={resp.code} msg={resp.msg}")
            break


def _send_to_chat(chat_id: str, text: str) -> None:
    """主动发消息到会话（供 reminder 推送等场景使用）。"""
    if not text:
        return
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = _api_client.im.v1.message.create(req)
    if not resp.success():
        print(f"[feishu] 主动发送失败 code={resp.code} msg={resp.msg}")


# ── 事件处理 ──────────────────────────────────────────────────────────────────

WHOAMI_MODE = False  # 命令行 --whoami 模式：把每条消息的 open_id 都回显


def _extract_text(content_json: str) -> str:
    """从飞书 message.content（JSON 字符串）提取纯文本，剥掉 @ 提及。"""
    try:
        obj = json.loads(content_json or "{}")
    except Exception:
        return ""
    text = obj.get("text", "") or ""
    # 飞书的 @ 提及在文本里是 "@_user_1"，去掉
    text = re.sub(r"@_user_\d+\s*", "", text)
    return text.strip()


def _handle_message(data: P2ImMessageReceiveV1) -> None:
    try:
        ev = data.event
        msg = ev.message
        sender = ev.sender

        sender_open_id = (sender.sender_id.open_id or "") if sender and sender.sender_id else ""
        message_id = msg.message_id
        msg_type = msg.message_type
        chat_id = msg.chat_id
        chat_type = msg.chat_type  # "p2p" 或 "group"

        # 只处理文本（其他类型先忽略）
        if msg_type != "text":
            _reply_text(message_id, f"(暂不支持的消息类型: {msg_type}，目前只接收文本)")
            return

        text = _extract_text(msg.content)
        if not text:
            return

        print(f"[feishu] 收到消息 from open_id={sender_open_id[:12]}… chat_type={chat_type} text={text[:60]!r}")

        # whoami 调试模式：直接回 open_id
        if WHOAMI_MODE:
            _reply_text(
                message_id,
                f"你的 open_id 是:\n{sender_open_id}\n\n请把它加入 config.json 的 feishu_allowed_open_ids 后重启 bot。",
            )
            return

        # 白名单
        if ALLOWED_OPEN_IDS and sender_open_id not in ALLOWED_OPEN_IDS:
            print(f"[feishu] ⚠ 未授权 open_id：{sender_open_id}（请加入 feishu_allowed_open_ids）")
            _reply_text(
                message_id,
                "⚠️ 你不在该机器人的允许列表中。\n"
                f"如果是你本人请把这个 open_id 加入 config.json 的 feishu_allowed_open_ids:\n{sender_open_id}",
            )
            return

        if not ALLOWED_OPEN_IDS:
            # 白名单为空时也提示（一次性引导）
            print(
                f"[feishu] ℹ 白名单为空，已允许所有人；建议把此 open_id 加入白名单：{sender_open_id}"
            )

        sess, lock = _get_session(sender_open_id)
        if not lock.acquire(blocking=False):
            _reply_text(message_id, "⏳ 上一条消息还在处理中，请稍候…")
            return
        try:
            reply = _capture_turn(sess, text)
        except Exception as e:
            print(f"[feishu] 处理出错：{e}")
            _reply_text(message_id, f"❌ 出错了：{e}")
            return
        finally:
            lock.release()

        _reply_text(message_id, reply)
    except Exception as e:
        print(f"[feishu] handler 异常：{e}")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def _build_ws_client() -> lark.ws.Client:
    event_handler = (
        lark.EventDispatcherHandler.builder("", "")  # 长连接无需 encrypt_key / token
        .register_p2_im_message_receive_v1(_handle_message)
        .build()
    )
    return lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )


def main() -> None:
    global WHOAMI_MODE

    parser = argparse.ArgumentParser(description="飞书机器人入口")
    parser.add_argument("--test", action="store_true", help="只测试凭据连通性")
    parser.add_argument("--whoami", action="store_true", help="把每位发送者的 open_id 回显给他自己")
    args = parser.parse_args()

    if args.test:
        # 测 token 是否能换取，证明 app_id/secret 没填错
        from lark_oapi.core.http.transport import Transport
        try:
            tenant_token = _api_client._config.token_manager.get_tenant_access_token()  # type: ignore
            if tenant_token:
                print(f"✅ 凭据 OK，tenant_access_token 已获取（前 8 位）：{tenant_token[:8]}…")
                sys.exit(0)
            print("❌ 未能获取 tenant_access_token，请检查 App ID / App Secret")
            sys.exit(1)
        except Exception as e:
            print(f"❌ 凭据校验失败：{e}")
            sys.exit(1)

    WHOAMI_MODE = args.whoami
    if WHOAMI_MODE:
        print("⚙️  WHOAMI 模式：bot 会把每个发送者的 open_id 原样回显，不调 agent")

    client = _build_ws_client()
    print(f"✅ 飞书 bot 已启动（App ID: {APP_ID[:10]}…），等待消息…")
    if not ALLOWED_OPEN_IDS:
        print("ℹ️  feishu_allowed_open_ids 为空，所有人均可对话。建议加白名单后重启。")
    client.start()  # 阻塞，内部 WS 自动重连


if __name__ == "__main__":
    main()
