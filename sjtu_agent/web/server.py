"""
sjtu_agent/web/server.py — 本地 Web 配置界面 HTTP Server

使用方式：
    sjtu-agent web                # 启动后自动打开浏览器
    sjtu-agent web --port 8080    # 自定义端口
    sjtu-agent web --no-browser   # 不自动打开浏览器
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from sjtu_agent.paths import ENV_PATH, CONFIG_PATH, AGENT_CONFIG_PATH

STATIC_DIR = Path(__file__).resolve().parent / "static"

# ── 预设 API 提供商 ──────────────────────────────────────────────────────────

PRESETS = {
    "zhiyuan": {
        "label": "致远一号（交大官方）",
        "base_url": "https://models.sjtu.edu.cn/api/v1",
        "model": "deepseek-chat",
        "env_key": "ZHIYUAN_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-5",
        "env_key": "ANTHROPIC_API_KEY",
    },
    "custom": {
        "label": "自定义",
        "base_url": "",
        "model": "",
        "env_key": "OPENAI_API_KEY",
    },
}


# ── .env 读写 ────────────────────────────────────────────────────────────────

def _read_env() -> dict[str, str]:
    """读取 .env 文件，返回 key→value 字典（不含注释）。"""
    result: dict[str, str] = {}
    if not ENV_PATH.exists():
        return result
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _write_env(updates: dict[str, str]) -> None:
    """将 updates 中的键值合并写回 .env，保留原有行的顺序和注释。"""
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    written_keys: set[str] = set()

    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                lines.append(line)
                continue
            if "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    lines.append(f'{k}={updates[k]}')
                    written_keys.add(k)
                    continue
            lines.append(line)

    for k, v in updates.items():
        if k not in written_keys:
            lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_config() -> dict:
    """读取 config.json，失败返回空字典。"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_config(updates: dict) -> None:
    """将 updates 深合并写回 config.json。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = _read_config()
    cfg.update(updates)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_agent_config() -> dict:
    if not AGENT_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(AGENT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_agent_config(data: dict) -> None:
    AGENT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 脱敏显示 ─────────────────────────────────────────────────────────────────

def _mask(value: str, keep: int = 4) -> str:
    """保留前 keep 个字符，其余替换为 *。"""
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * min(len(value) - keep, 20)


# ── API 状态检测 ──────────────────────────────────────────────────────────────

def _get_status() -> dict:
    """返回各项配置是否就绪。"""
    env = _read_env()
    cfg = _read_config()
    agent_cfg = _read_agent_config()

    # 判断 LLM API 是否配置
    has_zhiyuan = bool(env.get("ZHIYUAN_API_KEY"))
    has_deepseek = bool(env.get("DEEPSEEK_API_KEY"))
    has_openai = bool(env.get("OPENAI_API_KEY") or agent_cfg.get("api_key"))
    has_anthropic = bool(env.get("ANTHROPIC_API_KEY"))
    has_api = has_zhiyuan or has_deepseek or has_openai or has_anthropic

    # Canvas
    has_canvas = bool(cfg.get("canvas_token") and not cfg.get("canvas_token", "").startswith("YOUR_"))

    # jAccount
    has_jaccount = bool(env.get("JACCOUNT_USERNAME") and env.get("JACCOUNT_PASSWORD"))

    # Telegram
    has_telegram = bool(cfg.get("telegram_token"))

    # Feishu / Lark
    has_feishu = bool(cfg.get("feishu_app_id") and cfg.get("feishu_app_secret"))

    # aihaoke / phycai cookies
    has_aihaoke = bool(cfg.get("aihaoke_cookies", {}).get("haoke-token"))
    has_phycai = bool(cfg.get("phycai_cookies"))

    # MOOC
    has_mooc = bool(env.get("MOOC_USERNAME") and env.get("MOOC_PASSWORD"))

    # WeChat (ilink) — token is auto-saved after QR scan
    has_wechat = bool(cfg.get("wechat_bot_token"))

    # Push channel toggles
    telegram_enabled = bool(cfg.get("telegram_enabled", True))
    wechat_enabled = bool(cfg.get("wechat_enabled", True))
    feishu_enabled = bool(cfg.get("feishu_enabled", True))

    return {
        "api": has_api,
        "canvas": has_canvas,
        "jaccount": has_jaccount,
        "telegram": has_telegram,
        "feishu": has_feishu,
        "aihaoke": has_aihaoke,
        "phycai": has_phycai,
        "mooc": has_mooc,
        "wechat": has_wechat,
        "zhiyuan": has_zhiyuan,
        "deepseek": has_deepseek,
        "openai": has_openai,
        "anthropic": has_anthropic,
        "telegram_enabled": telegram_enabled,
        "wechat_enabled": wechat_enabled,
        "feishu_enabled": feishu_enabled,
    }


def _get_config_values() -> dict:
    """返回当前配置值（API Key 脱敏）。"""
    env = _read_env()
    cfg = _read_config()
    agent_cfg = _read_agent_config()

    # agent_config.json 是用户在 Web UI 中显式保存的配置，应优先回显。
    saved_provider = str(agent_cfg.get("provider", "") or "").strip().lower()
    provider = saved_provider if saved_provider in PRESETS else ""
    if not provider and agent_cfg.get("api_key"):
        provider = "custom"
    elif not provider and env.get("ZHIYUAN_API_KEY"):
        provider = "zhiyuan"
    elif not provider and env.get("DEEPSEEK_API_KEY"):
        provider = "deepseek"
    elif not provider and env.get("ANTHROPIC_API_KEY"):
        provider = "anthropic"
    elif not provider and env.get("OPENAI_API_KEY"):
        provider = "openai"
    elif not provider:
        provider = "custom"

    # 从 agent_config.json 读 base_url / model
    base_url = agent_cfg.get("base_url", "")
    model = agent_cfg.get("model", "")

    # 如果 agent_config 没有，根据 provider 给默认值
    if not base_url and provider in PRESETS:
        base_url = PRESETS[provider]["base_url"]
    if not model and provider in PRESETS:
        model = PRESETS[provider]["model"]

    # API key（脱敏）
    raw_key = agent_cfg.get("api_key", "")
    if not raw_key:
        if provider == "zhiyuan":
            raw_key = env.get("ZHIYUAN_API_KEY", "")
        elif provider == "deepseek":
            raw_key = env.get("DEEPSEEK_API_KEY", "")
        elif provider == "anthropic":
            raw_key = env.get("ANTHROPIC_API_KEY", "")
        elif provider == "openai":
            raw_key = env.get("OPENAI_API_KEY", "")

    return {
        "provider": provider,
        "api_key_masked": _mask(raw_key),
        "api_key_set": bool(raw_key),
        "base_url": base_url,
        "model": model,
        "jaccount_username": env.get("JACCOUNT_USERNAME", ""),
        "jaccount_password_set": bool(env.get("JACCOUNT_PASSWORD")),
        "canvas_token_masked": _mask(cfg.get("canvas_token", "")),
        "canvas_token_set": bool(cfg.get("canvas_token") and not cfg.get("canvas_token", "").startswith("YOUR_")),
        "canvas_base_url": cfg.get("canvas_base_url", "https://oc.sjtu.edu.cn"),
        "mooc_username": env.get("MOOC_USERNAME", ""),
        "mooc_password_set": bool(env.get("MOOC_PASSWORD")),
        "telegram_token_masked": _mask(cfg.get("telegram_token", "")),
        "telegram_token_set": bool(cfg.get("telegram_token")),
        "telegram_allowed_ids": cfg.get("telegram_allowed_ids", []),
        "feishu_app_id": cfg.get("feishu_app_id", ""),
        "feishu_app_id_set": bool(cfg.get("feishu_app_id")),
        "feishu_app_secret_masked": _mask(cfg.get("feishu_app_secret", "")),
        "feishu_app_secret_set": bool(cfg.get("feishu_app_secret")),
        "feishu_allowed_open_ids": cfg.get("feishu_allowed_open_ids", []),
        "wechat_set": bool(cfg.get("wechat_bot_token")),
        "telegram_enabled": bool(cfg.get("telegram_enabled", True)),
        "wechat_enabled": bool(cfg.get("wechat_enabled", True)),
        "feishu_enabled": bool(cfg.get("feishu_enabled", True)),
        "presets": PRESETS,
    }


# ── 聊天会话（内存，单用户） ────────────────────────────────────────────────────

MAX_TOOL_ROUNDS = 20
MAX_TOOL_CALLS = 12
REPEATED_TOOL_LIMIT = 3
_LLM_TRANSIENT_RETRY_DELAYS = (2.0, 5.0)

_chat_history: list[dict] = []   # [{role, content}, ...]
_chat_lock = threading.Lock()


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _token_event(text: str) -> str:
    return _sse({"token": text})


def _done_event() -> str:
    return _sse({"done": True})


def _canonical_tool_signature(name: str, args: dict) -> str:
    try:
        args_text = json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
    except Exception:
        args_text = "{}"
    return f"{name}:{args_text}"


def _tool_label(_agent, tool_name: str) -> str:
    labels = getattr(_agent, "_TOOL_LABELS", {})
    return labels.get(tool_name, tool_name)


def _summarize_tool_args(name: str, args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    parts: list[str] = []
    course = args.get("course")
    if course:
        parts.append(f"course={course}")
    include = args.get("include")
    if include:
        if isinstance(include, list):
            include_text = ",".join(str(item) for item in include[:4])
        else:
            include_text = str(include)
        parts.append(f"include={include_text}")
    limit = args.get("limit")
    if limit is not None:
        parts.append(f"limit={limit}")
    course_limit = args.get("course_limit")
    if course_limit is not None:
        parts.append(f"course_limit={course_limit}")
    include_past = args.get("include_past")
    if include_past:
        parts.append("含过去项目")
    return "；".join(parts[:4])


def _result_preview(result: str, max_chars: int = 500) -> str:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return text[:max_chars] if len(text) > max_chars else text


def _is_transient_llm_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "concurrency limit exceeded",
            "rate limit",
            "too many requests",
            "please retry later",
        )
    )


def _llm_busy_reply() -> str:
    return (
        "模型服务现在有点忙，同一个账号的并发请求被上游限制了。\n"
        "我已经自动重试过，还是没抢到空位。请稍等半分钟后再发一次。"
    )


def _tool_budget_reply(max_tool_calls: int = MAX_TOOL_CALLS) -> str:
    return (
        f"工具调用次数已经达到上限（{max_tool_calls} 次），我先停止继续查找，避免网页端一直卡住。\n"
        "如果你要看 Canvas 的全局 quiz、通知和最近任务，请直接说“Canvas 总览”；"
        "我会优先使用 get_canvas_overview 一次性汇总。"
    )


def _repeated_tool_reply(_agent, tool_name: str) -> str:
    label = _tool_label(_agent, tool_name)
    return (
        f"连续调用「{label}」仍未得到可继续推进的结果。\n"
        "这通常是课程名没有匹配到 Canvas 课程，或当前查询条件过于模糊。\n"
        "请尽量改用课程代码/Canvas 课程名再试，例如 `ECE2300`。"
    )


def _max_rounds_reply() -> str:
    return (
        "这轮对话的工具调用次数过多，我先暂停以避免一直卡住。\n"
        "请把问题缩小一点，或直接提供课程代码/作业名称后再试。"
    )


class _TurnState:
    def __init__(self, _agent, max_tool_calls: int = MAX_TOOL_CALLS):
        self._agent = _agent
        self.max_tool_calls = max_tool_calls
        self.total_tool_calls = 0
        self.tool_counts: dict[str, int] = {}

    def next_tool_start(self, name: str, args: dict) -> dict | None:
        if self.total_tool_calls >= self.max_tool_calls:
            return None
        self.total_tool_calls += 1
        return {
            "id": f"tool-{self.total_tool_calls}",
            "index": self.total_tool_calls,
            "name": name,
            "label": _tool_label(self._agent, name),
            "summary": _summarize_tool_args(name, args),
            "input": args,
        }

    def record_signature(self, name: str, args: dict) -> bool:
        signature = _canonical_tool_signature(name, args)
        self.tool_counts[signature] = self.tool_counts.get(signature, 0) + 1
        return self.tool_counts[signature] >= REPEATED_TOOL_LIMIT


def _get_chat_client():
    """根据当前配置创建客户端。
    与 agent.py 的 _make_client 保持一致：
    - claude 模型 → Anthropic SDK（带 claude-cli UA，兼容中转代理）
    - 其他模型   → OpenAI SDK
    """
    agent_cfg = _read_agent_config()
    env = _read_env()
    provider = str(agent_cfg.get("provider", "") or "").strip().lower()
    base_url = agent_cfg.get("base_url") or None
    model = agent_cfg.get("model", "deepseek-chat")
    ua = agent_cfg.get("user_agent", "claude-cli/1.0.57")
    api_key = agent_cfg.get("api_key", "")

    if model.startswith("claude"):
        api_key = api_key or env.get("ANTHROPIC_API_KEY", "")
        from anthropic import Anthropic
        client = Anthropic(
            api_key=api_key,
            base_url=base_url,
            default_headers={"user-agent": ua},
            timeout=120.0,
        )
        return client, model, "anthropic"

    if not api_key:
        if provider == "zhiyuan":
            api_key = env.get("ZHIYUAN_API_KEY", "")
        elif provider == "deepseek":
            api_key = env.get("DEEPSEEK_API_KEY", "")
        elif provider == "openai":
            api_key = env.get("OPENAI_API_KEY", "")
        elif provider == "custom":
            api_key = ""
        else:
            api_key = (
                env.get("ZHIYUAN_API_KEY")
                or env.get("DEEPSEEK_API_KEY")
                or env.get("OPENAI_API_KEY")
                or ""
            )

    if model.startswith("claude"):
        from anthropic import Anthropic
        client = Anthropic(
            api_key=api_key,
            base_url=base_url,
            default_headers={"user-agent": ua},
            timeout=120.0,
        )
        return client, model, "anthropic"
    else:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0)
        return client, model, "openai"


def _stream_chat(user_message: str):
    """生成器：将 user_message 发给 LLM，支持 tool_use 循环，以 SSE 格式 yield 数据行。"""
    import datetime as _dt
    global _chat_history

    if not _chat_lock.acquire(blocking=False):
        yield _token_event("上一轮对话还在运行，请等它结束后再发送新消息。")
        yield _done_event()
        return

    try:
        yield _sse({"status": {"phase": "thinking", "message": "正在思考…"}})

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        import agent as _agent

        if not _chat_history:
            _now = _dt.datetime.now()
            _date_ctx = (
                f"\n\n## 当前时间\n"
                f"现在：{_now.strftime('%Y年%m月%d日 %H:%M')}，星期{'一二三四五六日'[_now.weekday()]}。"
            )
            _chat_history.append({"role": "system", "content": _agent.SYSTEM_PROMPT + _date_ctx})

        _chat_history.append({"role": "user", "content": user_message})

        try:
            client, model, proto = _get_chat_client()
        except Exception as exc:
            if _chat_history and _chat_history[-1]["role"] == "user":
                _chat_history.pop()
            yield _sse({"error": f"创建客户端失败：{exc}"})
            yield _done_event()
            return

        state = _TurnState(_agent)
        try:
            if proto == "anthropic":
                yield from _stream_chat_anthropic(client, model, _agent, MAX_TOOL_ROUNDS, state)
            else:
                yield from _stream_chat_openai(client, model, _agent, MAX_TOOL_ROUNDS, state)
        except Exception as exc:
            yield _sse({"error": str(exc)})

        yield _done_event()
    finally:
        _chat_lock.release()


def _stream_chat_anthropic(client, model, _agent, max_rounds, state: _TurnState):
    """Anthropic Messages API 流式 + tool_use 循环。"""
    global _chat_history

    system_msg = ""
    for m in _chat_history:
        if m["role"] == "system":
            system_msg = m["content"]
            break
    api_msgs = [m for m in _chat_history if m["role"] != "system"]
    tools = _agent._anthropic_tools()

    for _round in range(max_rounds):
        full_text = ""
        content_blocks: list[dict] = []
        tool_inputs: dict[int, str] = {}

        try:
            with client.messages.stream(
                model=model,
                max_tokens=4096,
                system=system_msg,
                messages=api_msgs,
                tools=tools,
            ) as stream:
                for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "content_block_start":
                        block = event.content_block
                        btype = getattr(block, "type", "")
                        if btype == "text":
                            content_blocks.append({"type": "text", "text": ""})
                        elif btype == "tool_use":
                            content_blocks.append({
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": {},
                            })
                            tool_inputs[len(content_blocks) - 1] = ""
                    elif etype == "content_block_delta":
                        delta = event.delta
                        dtype = getattr(delta, "type", "")
                        if dtype == "text_delta":
                            chunk = delta.text
                            full_text += chunk
                            if content_blocks and content_blocks[-1].get("type") == "text":
                                content_blocks[-1]["text"] += chunk
                            yield _sse({"token": chunk})
                        elif dtype == "input_json_delta":
                            idx = event.index
                            tool_inputs[idx] = tool_inputs.get(idx, "") + delta.partial_json
        except Exception as exc:
            if _chat_history and _chat_history[-1]["role"] == "user":
                _chat_history.pop()
            yield _sse({"error": str(exc)})
            return

        # 解析 tool_use input
        for idx, raw_json in tool_inputs.items():
            if idx < len(content_blocks) and content_blocks[idx].get("type") == "tool_use":
                try:
                    content_blocks[idx]["input"] = json.loads(raw_json or "{}")
                except Exception:
                    content_blocks[idx]["input"] = {}

        has_tool_use = any(b.get("type") == "tool_use" for b in content_blocks)
        api_msgs.append({"role": "assistant", "content": content_blocks})

        if not has_tool_use:
            _chat_history.append({"role": "assistant", "content": full_text})
            return

        # 执行工具
        tool_results = []
        for b in content_blocks:
            if b.get("type") != "tool_use":
                continue
            fn_name = b["name"]
            fn_args = b["input"] if isinstance(b["input"], dict) else {}
            yield _sse({"tool_start": {"name": fn_name, "input": fn_args}})
            result = _agent.run_tool(fn_name, fn_args)
            result_preview = result[:500] if len(result) > 500 else result
            yield _sse({"tool_end": {"name": fn_name, "result": result_preview}})
            tool_results.append({"type": "tool_result", "tool_use_id": b["id"], "content": result})
        api_msgs.append({"role": "user", "content": tool_results})

    # 超出 max_rounds 仍在调工具：强制一次无工具调用合成最终回复
    try:
        fb_text = ""
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=system_msg,
            messages=api_msgs,
        ) as stream:
            for event in stream:
                if getattr(event, "type", "") == "content_block_delta":
                    delta = event.delta
                    if getattr(delta, "type", "") == "text_delta":
                        fb_text += delta.text
                        yield _sse({"token": delta.text})
        _chat_history.append({"role": "assistant", "content": fb_text or full_text})
    except Exception as exc:
        yield _sse({"error": str(exc)})
        _chat_history.append({"role": "assistant", "content": full_text})


def _stream_chat_openai(client, model, _agent, max_rounds, state: _TurnState):
    """OpenAI Chat Completions 流式 + tool_calls 循环。"""
    global _chat_history

    messages = list(_chat_history)
    full_text_all = ""

    for _round in range(max_rounds):
        attempts = len(_LLM_TRANSIENT_RETRY_DELAYS) + 1
        text_so_far = ""
        tool_calls_map: dict[int, dict] = {}

        for attempt in range(attempts):
            text_so_far = ""
            tool_calls_map = {}
            attempt_text_chunks: list[str] = []
            try:
                stream = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=_agent.TOOLS,
                    tool_choice="auto",
                    stream=True,
                    timeout=180,
                )
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        text_so_far += delta.content
                        attempt_text_chunks.append(delta.content)
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_map:
                                tool_calls_map[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                            if tc.id:
                                tool_calls_map[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_map[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_map[idx]["arguments"] += tc.function.arguments
                for text in attempt_text_chunks:
                    full_text_all += text
                    yield _token_event(text)
                break
            except Exception as exc:
                if not _is_transient_llm_error(exc):
                    if _chat_history and _chat_history[-1]["role"] == "user":
                        _chat_history.pop()
                    yield _sse({"error": str(exc)})
                    return
                if attempt >= attempts - 1:
                    reply = _llm_busy_reply()
                    _chat_history.append({"role": "assistant", "content": reply})
                    yield _token_event(reply)
                    return
                delay = _LLM_TRANSIENT_RETRY_DELAYS[attempt]
                yield _sse({
                    "retry": {
                        "attempt": attempt + 1,
                        "delay_s": delay,
                        "message": f"模型服务忙，{delay:g}s 后自动重试…",
                    }
                })
                time.sleep(delay)

        if not tool_calls_map:
            _chat_history.append({"role": "assistant", "content": text_so_far})
            messages.append({"role": "assistant", "content": text_so_far})
            return

        tool_calls_payload = []
        for idx in sorted(tool_calls_map):
            entry = tool_calls_map[idx]
            tool_calls_payload.append({
                "id": entry["id"] or f"call_{idx + 1}",
                "type": "function",
                "function": {"name": entry["name"], "arguments": entry["arguments"]},
            })

        assistant_msg = {
            "role": "assistant",
            "content": text_so_far or None,
            "tool_calls": tool_calls_payload,
        }
        messages.append(assistant_msg)

        repeated_tool_name = ""
        for tc in tool_calls_payload:
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception:
                fn_args = {}

            start_payload = state.next_tool_start(fn_name, fn_args)
            if start_payload is None:
                reply = _tool_budget_reply(state.max_tool_calls)
                yield _sse({
                    "tool_limit": {
                        "max_tool_calls": state.max_tool_calls,
                        "message": "工具调用已到上限，正在整理已有结果…",
                    }
                })
                _chat_history.append({"role": "assistant", "content": reply})
                yield _token_event(reply)
                return

            yield _sse({"tool_start": start_payload})
            t0 = time.monotonic()
            try:
                result = _agent.run_tool(fn_name, fn_args)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                yield _sse({
                    "tool_end": {
                        "id": start_payload["id"],
                        "index": start_payload["index"],
                        "name": fn_name,
                        "label": start_payload["label"],
                        "elapsed_ms": elapsed_ms,
                        "result_preview": _result_preview(result),
                    }
                })
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                result = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                yield _sse({
                    "tool_end": {
                        "id": start_payload["id"],
                        "index": start_payload["index"],
                        "name": fn_name,
                        "label": start_payload["label"],
                        "elapsed_ms": elapsed_ms,
                        "error": str(exc),
                        "result_preview": result,
                    }
                })

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

            if state.record_signature(fn_name, fn_args) and not repeated_tool_name:
                repeated_tool_name = fn_name

        if repeated_tool_name:
            reply = _repeated_tool_reply(_agent, repeated_tool_name)
            _chat_history.append({"role": "assistant", "content": reply})
            yield _token_event(reply)
            return

    reply = _max_rounds_reply()
    yield _token_event(reply)
    _chat_history.append({"role": "assistant", "content": reply})


def _test_api(api_key: str, base_url: str, model: str) -> dict:
    """发送一条测试消息，返回是否成功和延迟。"""
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "openai 包未安装"}

    if not api_key:
        return {"ok": False, "error": "API Key 为空"}
    if not base_url:
        return {"ok": False, "error": "Base URL 为空"}
    if not model:
        return {"ok": False, "error": "模型名为空"}

    try:
        import httpx
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=15.0,  # 15s 超时，防止卡住
        )
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        latency = int((time.time() - t0) * 1000)
        # 部分非标准 API 实现可能返回 str 或其他类型
        if hasattr(resp, "choices") and resp.choices:
            reply = resp.choices[0].message.content or ""
        else:
            reply = str(resp)[:80]
        return {"ok": True, "latency_ms": latency, "reply": reply}
    except Exception as exc:
        err = str(exc)
        # 提取关键错误信息
        if "401" in err or "Unauthorized" in err or "invalid_api_key" in err.lower():
            err = "API Key 无效（401 Unauthorized）"
        elif "timed out" in err.lower() or "timeout" in err.lower():
            err = "连接超时（15s），请检查 Base URL 是否正确"
        elif "Connection" in err or "connect" in err.lower():
            err = "无法连接到服务器，请检查 Base URL 和网络"
        elif "model" in err.lower() and ("not found" in err.lower() or "not exist" in err.lower()):
            err = f"模型 '{model}' 不存在，请确认模型名称"
        elif "choices" in err:
            err = "API 响应格式不兼容（非 OpenAI 标准格式）"
        return {"ok": False, "error": err}


def _wechat_qr_status(qrcode_key: str, ilink_base: str) -> dict:
    """轮询微信二维码扫码状态，扫码成功后保存 token 并尝试启动守护进程。"""
    try:
        import httpx as _httpx
        resp = _httpx.get(
            f"{ilink_base}/ilink/bot/get_qrcode_status?qrcode={qrcode_key}",
            timeout=10,
        )
        status = resp.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}

    code = status.get("code", -1)
    if code == 0:
        token      = status["bot_token"]
        account_id = status.get("account_id", "")
        user_id    = status.get("user_id", "")
        cfg = _read_config()
        cfg["wechat_bot_token"]   = token
        cfg["wechat_account_id"] = account_id
        cfg["wechat_user_id"]    = user_id
        _write_config(cfg)
        started = False
        try:
            import subprocess as _sp, os as _os
            uid = _os.getuid()
            r = _sp.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/com.sjtu.wechat-bot"],
                capture_output=True, timeout=10,
            )
            started = r.returncode == 0
        except Exception:
            pass
        return {"status": "success", "daemon_started": started}
    elif code in (1, 2):
        return {"status": "pending"}
    else:
        return {"status": "expired", "code": code}


def _start_feishu_bot() -> dict:
    """安装（若需要）并通过 launchctl 启动飞书 bot。"""
    import sys as _sys
    if _sys.platform != "darwin":
        return {"ok": False, "error": "目前仅支持 macOS 通过 launchd 自动启动；其他平台请手动运行 `sjtu-agent feishu-bot`。"}
    cfg = _read_config()
    if not (cfg.get("feishu_app_id") and cfg.get("feishu_app_secret")):
        return {"ok": False, "error": "请先填写并保存飞书 App ID / App Secret"}
    try:
        from sjtu_agent.scheduler import install_daemons
        from pathlib import Path as _P
        install_daemons(
            service_names=("feishu-bot",),
            python_executable=_P(_sys.executable),
            load=True,
        )
    except Exception as exc:
        return {"ok": False, "error": f"安装 launchd 服务失败：{exc}"}
    try:
        import subprocess as _sp, os as _os
        uid = _os.getuid()
        r = _sp.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/com.sjtu.feishu-bot"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return {"ok": True, "message": "✅ 飞书 bot 已启动；现在可以在飞书里搜索 bot 私聊试试。"}
        return {"ok": False, "error": f"launchctl kickstart 失败：{r.stderr.strip() or r.stdout.strip()}"}
    except Exception as exc:
        return {"ok": False, "error": f"launchctl 调用失败：{exc}"}


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # 静默日志，只打印错误
        pass

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        ext = path.suffix.lower()
        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html")
        elif path == "/api/config":
            self._send_json(_get_config_values())
        elif path == "/api/status":
            self._send_json(_get_status())
        elif path == "/api/wechat/qr_status":
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            key = qs.get("key", [""])[0]
            ilink_base = qs.get("ilink_base", ["https://ilinkai.weixin.qq.com"])[0]
            if not key:
                self._send_json({"status": "error", "error": "missing key"}, 400)
            else:
                self._send_json(_wechat_qr_status(key, ilink_base))
        else:
            # 尝试静态文件
            file_path = STATIC_DIR / path.lstrip("/")
            if file_path.exists() and file_path.is_file():
                self._send_file(file_path)
            else:
                self.send_response(404)
                self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config/save":
            body = self._read_body()
            self._handle_save(body)
        elif path == "/api/test/api":
            body = self._read_body()
            result = _test_api(
                api_key=body.get("api_key", ""),
                base_url=body.get("base_url", ""),
                model=body.get("model", ""),
            )
            self._send_json(result)
        elif path == "/api/chat":
            body = self._read_body()
            user_msg = body.get("message", "").strip()
            if not user_msg:
                self._send_json({"error": "消息不能为空"}, 400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            for chunk in _stream_chat(user_msg):
                try:
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
        elif path == "/api/chat/clear":
            global _chat_history
            if not _chat_lock.acquire(blocking=False):
                self._send_json({"ok": False, "error": "上一轮对话还在运行，请等它结束后再清空。"}, 409)
                return
            try:
                _chat_history = []
                self._send_json({"ok": True})
            finally:
                _chat_lock.release()
        elif path == "/api/feishu/start":
            self._send_json(_start_feishu_bot())
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_save(self, body: dict) -> None:
        section = body.get("section", "")
        try:
            if section == "api":
                self._save_api(body)
            elif section == "credentials":
                self._save_credentials(body)
            elif section == "telegram":
                self._save_telegram(body)
            elif section == "feishu":
                self._save_feishu(body)
            elif section == "push_channels":
                self._save_push_channels(body)
            else:
                self._send_json({"ok": False, "error": f"未知 section: {section}"}, 400)
                return
            self._send_json({"ok": True})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)

    def _save_api(self, body: dict) -> None:
        provider = body.get("provider", "custom")
        api_key = body.get("api_key", "").strip()
        base_url = body.get("base_url", "").strip()
        model = body.get("model", "").strip()

        # 写 .env
        env_updates: dict[str, str] = {}
        if api_key:
            preset = PRESETS.get(provider, PRESETS["custom"])
            env_key = preset["env_key"]
            if provider == "zhiyuan":
                env_updates["ZHIYUAN_API_KEY"] = api_key
            elif provider == "deepseek":
                env_updates["DEEPSEEK_API_KEY"] = api_key
            elif provider == "anthropic":
                env_updates["ANTHROPIC_API_KEY"] = api_key
            elif provider == "openai":
                env_updates[env_key] = api_key
        if env_updates:
            _write_env(env_updates)

        # 写 agent_config.json
        agent_cfg = _read_agent_config()
        agent_cfg["provider"] = provider if provider in PRESETS else "custom"
        if base_url:
            agent_cfg["base_url"] = base_url
        if model:
            agent_cfg["model"] = model
        if api_key and provider in ("custom", "openai", "anthropic"):
            agent_cfg["api_key"] = api_key
        elif provider == "zhiyuan":
            agent_cfg.pop("api_key", None)
        _write_agent_config(agent_cfg)

    def _save_credentials(self, body: dict) -> None:
        env_updates: dict[str, str] = {}
        cfg_updates: dict = {}

        if body.get("jaccount_username"):
            env_updates["JACCOUNT_USERNAME"] = body["jaccount_username"].strip()
        if body.get("jaccount_password"):
            env_updates["JACCOUNT_PASSWORD"] = body["jaccount_password"].strip()
        if body.get("mooc_username"):
            env_updates["MOOC_USERNAME"] = body["mooc_username"].strip()
        if body.get("mooc_password"):
            env_updates["MOOC_PASSWORD"] = body["mooc_password"].strip()

        if body.get("canvas_token"):
            cfg_updates["canvas_token"] = body["canvas_token"].strip()
        if body.get("canvas_base_url"):
            cfg_updates["canvas_base_url"] = body["canvas_base_url"].strip()

        if env_updates:
            _write_env(env_updates)
        if cfg_updates:
            _write_config(cfg_updates)

    def _save_telegram(self, body: dict) -> None:
        cfg_updates: dict = {}
        if body.get("telegram_token"):
            cfg_updates["telegram_token"] = body["telegram_token"].strip()
        if "telegram_allowed_ids" in body:
            ids = body["telegram_allowed_ids"]
            if isinstance(ids, list):
                cfg_updates["telegram_allowed_ids"] = [int(i) for i in ids if str(i).strip().lstrip("-").isdigit()]
            elif isinstance(ids, str):
                raw_ids = [i.strip() for i in re.split(r"[,\s]+", ids) if i.strip()]
                cfg_updates["telegram_allowed_ids"] = [int(i) for i in raw_ids if i.lstrip("-").isdigit()]
        if cfg_updates:
            _write_config(cfg_updates)


    def _save_feishu(self, body: dict) -> None:
        cfg_updates: dict = {}
        if body.get("feishu_app_id"):
            cfg_updates["feishu_app_id"] = body["feishu_app_id"].strip()
        if body.get("feishu_app_secret"):
            cfg_updates["feishu_app_secret"] = body["feishu_app_secret"].strip()
        if "feishu_allowed_open_ids" in body:
            ids = body["feishu_allowed_open_ids"]
            if isinstance(ids, list):
                cfg_updates["feishu_allowed_open_ids"] = [str(i).strip() for i in ids if str(i).strip()]
            elif isinstance(ids, str):
                raw_ids = [i.strip() for i in re.split(r"[,\s]+", ids) if i.strip()]
                cfg_updates["feishu_allowed_open_ids"] = raw_ids
        if cfg_updates:
            _write_config(cfg_updates)

    def _save_push_channels(self, body: dict) -> None:
        cfg_updates: dict = {}
        if "telegram_enabled" in body:
            cfg_updates["telegram_enabled"] = bool(body["telegram_enabled"])
        if "wechat_enabled" in body:
            cfg_updates["wechat_enabled"] = bool(body["wechat_enabled"])
        if "feishu_enabled" in body:
            cfg_updates["feishu_enabled"] = bool(body["feishu_enabled"])
        if cfg_updates:
            _write_config(cfg_updates)

        # 后台同步守护进程状态（macOS 上自动启停 bot 服务）
        # 注意：不能调用 install_daemons，因为它会处理 web 服务并触发
        # launchctl bootout，导致当前 Web Server 进程被自杀。
        def _sync() -> None:
            import sys as _sys, os, subprocess, plistlib
            if _sys.platform != "darwin":
                return
            try:
                from pathlib import Path as _P
                from sjtu_agent.scheduler.launchd import (
                    _SERVICE_SPECS, _build_plist, _DEFAULT_OUTPUT_DIR,
                )

                out_dir = _P(_DEFAULT_OUTPUT_DIR).expanduser().resolve()
                py = _P(_sys.executable).absolute()
                uid = os.getuid()

                for key, enabled in cfg_updates.items():
                    name = {
                        "telegram_enabled": "telegram-bot",
                        "wechat_enabled": "wechat-bot",
                        "feishu_enabled": "feishu-bot",
                    }.get(key)
                    if name is None or name not in _SERVICE_SPECS:
                        continue
                    spec = _SERVICE_SPECS[name]
                    label = spec["label"]
                    plist_path = out_dir / f"{label}.plist"

                    if enabled:
                        payload = _build_plist(
                            name, py, (22, 0), 60, 10, (10, 0),
                        )
                        with plist_path.open("wb") as f:
                            plistlib.dump(payload, f, sort_keys=False)
                        for domain in (f"gui/{uid}", f"user/{uid}"):
                            subprocess.run(
                                ["launchctl", "bootout", f"{domain}/{label}"],
                                capture_output=True,
                            )
                            r = subprocess.run(
                                ["launchctl", "bootstrap", domain, str(plist_path)],
                                capture_output=True, text=True,
                            )
                            if r.returncode == 0:
                                break
                    else:
                        for domain in (f"gui/{uid}", f"user/{uid}"):
                            subprocess.run(
                                ["launchctl", "bootout", f"{domain}/{label}"],
                                capture_output=True,
                            )
                        if plist_path.exists():
                            plist_path.unlink()
            except Exception:
                pass

        threading.Thread(target=_sync, daemon=True).start()


# ── 启动入口 ──────────────────────────────────────────────────────────────────

def start(port: int = 7860, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n🌐  SJTU Agent 配置界面已启动：{url}")
    print("     按 Ctrl+C 关闭\n")

    if open_browser:
        # 延迟 0.5s 再打开，确保 server 已就绪
        def _open():
            time.sleep(0.5)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n配置界面已关闭。")
        server.server_close()
