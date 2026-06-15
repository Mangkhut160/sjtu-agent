# Web Agent Streaming Tool Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local web agent stream transparent progress for thinking, tool calls, retries, limits, and errors while preventing runaway tool loops.

**Architecture:** Keep `/api/chat` as an SSE endpoint and add a small web-turn state object in `sjtu_agent/web/server.py` to track tool IDs, budgets, repeated signatures, timings, and retries. The frontend keeps streaming answer text but renders a compact progress timeline keyed by tool IDs, with expandable JSON details for each tool call.

**Tech Stack:** Python 3.10+, `http.server` / `ThreadingHTTPServer`, OpenAI-compatible chat completions, Anthropic Messages streaming, vanilla HTML/CSS/JavaScript, pytest.

---

## File Structure

- Modify `sjtu_agent/web/server.py`
  - Add backend SSE helpers, transient error handling, tool-call budget tracking, repeated-call detection, tool timings, and chat lock.
  - Update OpenAI and Anthropic streaming loops to emit structured progress events.
- Modify `sjtu_agent/web/static/index.html`
  - Add compact progress timeline styles.
  - Replace single-card tracking with a `Map` keyed by tool IDs.
  - Expand Canvas tool labels and render retry/limit/error status rows.
- Create `tests/test_web_streaming.py`
  - Unit tests for backend SSE events, budget stops, repeated stops, transient retries, and chat lock behavior.
- Create `tests/test_web_static.py`
  - Static frontend checks for Canvas labels, keyed tool map, timeline helpers, and removal of single `currentToolCard` tracking.

The current worktree already has unrelated WeChat/Canvas changes. Each task must stage only the files named in that task.

---

### Task 1: Backend Streaming Tests

**Files:**
- Create: `tests/test_web_streaming.py`
- Modify: none
- Test: `tests/test_web_streaming.py`

- [ ] **Step 1: Write failing backend tests**

Create `tests/test_web_streaming.py` with this content:

```python
from __future__ import annotations

import json
from types import SimpleNamespace


def _chunk(*, content: str = "", tool_calls=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tool_call(name: str, arguments: str = "{}", index: int = 0, call_id: str = "call_1"):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _events_from(chunks):
    events = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            block = block.strip()
            if not block.startswith("data: "):
                continue
            payload = block.removeprefix("data: ").strip()
            if payload == "[DONE]":
                events.append({"done": True})
                continue
            events.append(json.loads(payload))
    return events


class FakeOpenAIToolClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return [
                _chunk(tool_calls=[
                    _tool_call("get_canvas_todo", '{"limit": 1}'),
                    _tool_call(
                        "get_canvas_course_quizzes",
                        '{"course": "ECE2300"}',
                        index=1,
                        call_id="call_2",
                    ),
                ])
            ]
        return [_chunk(content="查好了。")]


class FakeOpenAILoopingClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **_kwargs):
        return [
            _chunk(tool_calls=[
                _tool_call("get_canvas_course_quizzes", '{"course": "电磁学"}')
            ])
        ]


class FakeOpenAIRotatingClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return [
            _chunk(tool_calls=[
                _tool_call(
                    "get_canvas_course_updates",
                    json.dumps({"course": f"COURSE{self.calls}"}, ensure_ascii=False),
                )
            ])
        ]


class FailingStream:
    def __iter__(self):
        if False:
            yield None
        raise RuntimeError("Concurrency limit exceeded for user, please retry later")


class FakeTransientLimitClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls < 3:
            return FailingStream()
        return [_chunk(content="现在可以继续了。")]


class FakePersistentLimitClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return FailingStream()


def _reset_web_server(monkeypatch, client):
    import agent
    import sjtu_agent.web.server as server

    server._chat_history = []
    if hasattr(server, "_chat_lock") and server._chat_lock.locked():
        server._chat_lock.release()
    monkeypatch.setattr(server, "_get_chat_client", lambda: (client, "deepseek-chat", "openai"))
    monkeypatch.setattr(agent, "run_tool", lambda name, args: '{"ok": true, "tool": "%s"}' % name)
    return server


def test_web_stream_chat_openai_reports_keyed_tool_progress(monkeypatch):
    server = _reset_web_server(monkeypatch, FakeOpenAIToolClient())

    events = _events_from(server._stream_chat("看看 Canvas 待办和 ECE2300 quiz"))

    starts = [event["tool_start"] for event in events if "tool_start" in event]
    ends = [event["tool_end"] for event in events if "tool_end" in event]
    tokens = [event["token"] for event in events if "token" in event]

    assert events[0]["status"]["phase"] == "thinking"
    assert [start["id"] for start in starts] == ["tool-1", "tool-2"]
    assert [start["index"] for start in starts] == [1, 2]
    assert starts[0]["label"] == "正在读取 Canvas 待办"
    assert starts[0]["summary"] == "limit=1"
    assert starts[1]["label"] == "正在读取 Canvas Quiz"
    assert starts[1]["summary"] == "course=ECE2300"
    assert [end["id"] for end in ends] == ["tool-1", "tool-2"]
    assert all(isinstance(end["elapsed_ms"], int) for end in ends)
    assert tokens == ["查好了。"]
    assert events[-1] == {"done": True}


def test_web_stream_chat_stops_repeated_tool_loop(monkeypatch):
    server = _reset_web_server(monkeypatch, FakeOpenAILoopingClient())

    events = _events_from(server._stream_chat("帮我看电磁学 quiz"))

    starts = [event for event in events if "tool_start" in event]
    tokens = "".join(event.get("token", "") for event in events)

    assert len(starts) == 3
    assert "连续调用" in tokens
    assert "Canvas Quiz" in tokens
    assert events[-1] == {"done": True}


def test_web_stream_chat_stops_after_total_tool_budget(monkeypatch):
    server = _reset_web_server(monkeypatch, FakeOpenAIRotatingClient())

    events = _events_from(server._stream_chat("把所有 Canvas 动态都查一下"))

    starts = [event for event in events if "tool_start" in event]
    limits = [event["tool_limit"] for event in events if "tool_limit" in event]
    tokens = "".join(event.get("token", "") for event in events)

    assert len(starts) == 12
    assert limits == [{
        "max_tool_calls": 12,
        "message": "工具调用已到上限，正在整理已有结果…",
    }]
    assert "工具调用次数已经达到上限" in tokens
    assert events[-1] == {"done": True}


def test_web_stream_chat_retries_transient_concurrency_limit(monkeypatch):
    client = FakeTransientLimitClient()
    server = _reset_web_server(monkeypatch, client)
    monkeypatch.setattr(server, "_LLM_TRANSIENT_RETRY_DELAYS", (0, 0), raising=False)

    events = _events_from(server._stream_chat("继续"))

    retries = [event["retry"] for event in events if "retry" in event]
    tokens = [event["token"] for event in events if "token" in event]

    assert client.calls == 3
    assert [retry["attempt"] for retry in retries] == [1, 2]
    assert tokens == ["现在可以继续了。"]
    assert events[-1] == {"done": True}


def test_web_stream_chat_returns_friendly_message_when_limit_persists(monkeypatch):
    client = FakePersistentLimitClient()
    server = _reset_web_server(monkeypatch, client)
    monkeypatch.setattr(server, "_LLM_TRANSIENT_RETRY_DELAYS", (0, 0), raising=False)

    events = _events_from(server._stream_chat("继续"))

    tokens = "".join(event.get("token", "") for event in events)

    assert client.calls == 3
    assert "模型服务现在有点忙" in tokens
    assert "Concurrency limit" not in tokens
    assert events[-1] == {"done": True}


def test_web_stream_chat_rejects_concurrent_turn(monkeypatch):
    server = _reset_web_server(monkeypatch, FakeOpenAIToolClient())
    server._chat_lock.acquire()
    try:
        events = _events_from(server._stream_chat("第二个请求"))
    finally:
        server._chat_lock.release()

    tokens = "".join(event.get("token", "") for event in events)

    assert "上一轮对话还在运行" in tokens
    assert events[-1] == {"done": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_web_streaming.py -q
```

Expected: FAIL. The current backend does not emit `status`, `id`, `label`, `summary`, `retry`, `tool_limit`, or `done` events, and `_chat_lock` does not exist yet.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_web_streaming.py
git commit -m "test: cover web agent streaming progress"
```

---

### Task 2: Backend Helpers and Chat Lock

**Files:**
- Modify: `sjtu_agent/web/server.py`
- Test: `tests/test_web_streaming.py`

- [ ] **Step 1: Add constants, lock, and helper functions**

In `sjtu_agent/web/server.py`, replace the chat session block:

```python
# ── 聊天会话（内存，单用户） ────────────────────────────────────────────────────

_chat_history: list[dict] = []   # [{role, content}, additional entries]
```

with:

```python
# ── 聊天会话（内存，单用户） ────────────────────────────────────────────────────

MAX_TOOL_ROUNDS = 20
MAX_TOOL_CALLS = 12
REPEATED_TOOL_LIMIT = 3
_LLM_TRANSIENT_RETRY_DELAYS = (2.0, 5.0)

_chat_history: list[dict] = []   # [{role, content}, additional entries]
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
```

- [ ] **Step 2: Update `_stream_chat` to use the lock and new done event**

Replace the whole `_stream_chat` function with:

```python
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
```

- [ ] **Step 3: Run the chat lock test**

Run:

```bash
pytest tests/test_web_streaming.py::test_web_stream_chat_rejects_concurrent_turn -q
```

Expected: PASS.

- [ ] **Step 4: Commit helper and lock changes**

```bash
git add sjtu_agent/web/server.py
git commit -m "feat: add web chat streaming state helpers"
```

---

### Task 3: OpenAI Streaming Loop Controls

**Files:**
- Modify: `sjtu_agent/web/server.py`
- Test: `tests/test_web_streaming.py`

- [ ] **Step 1: Replace `_stream_chat_openai`**

Replace the whole `_stream_chat_openai` function with:

```python
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
                        full_text_all += delta.content
                        yield _token_event(delta.content)
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

    reply = full_text_all or _max_rounds_reply()
    if full_text_all:
        reply = _max_rounds_reply()
        yield _token_event(reply)
    _chat_history.append({"role": "assistant", "content": reply})
```

- [ ] **Step 2: Run OpenAI streaming tests**

Run:

```bash
pytest tests/test_web_streaming.py -q
```

Expected: the OpenAI progress, repeated loop, budget, retry, persistent busy, and lock tests PASS.

- [ ] **Step 3: Run existing WeChat progress tests**

Run:

```bash
pytest tests/test_wechat_progress.py -q
```

Expected: PASS. This confirms the web changes did not break the patterns copied from the WeChat side.

- [ ] **Step 4: Commit OpenAI loop controls**

```bash
git add sjtu_agent/web/server.py
git commit -m "feat: stream web agent tool progress events"
```

---

### Task 4: Anthropic Streaming Parity

**Files:**
- Modify: `sjtu_agent/web/server.py`
- Test: `tests/test_web_streaming.py`

- [ ] **Step 1: Update `_stream_chat_anthropic` signature and body**

Replace the whole `_stream_chat_anthropic` function with:

```python
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
    full_text_all = ""

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
                            full_text_all += chunk
                            if content_blocks and content_blocks[-1].get("type") == "text":
                                content_blocks[-1]["text"] += chunk
                            yield _token_event(chunk)
                        elif dtype == "input_json_delta":
                            idx = event.index
                            tool_inputs[idx] = tool_inputs.get(idx, "") + delta.partial_json
        except Exception as exc:
            if _chat_history and _chat_history[-1]["role"] == "user":
                _chat_history.pop()
            yield _sse({"error": str(exc)})
            return

        for idx, raw_json in tool_inputs.items():
            if idx < len(content_blocks) and content_blocks[idx].get("type") == "tool_use":
                try:
                    content_blocks[idx]["input"] = json.loads(raw_json or "{}")
                except Exception:
                    content_blocks[idx]["input"] = {}

        api_msgs.append({"role": "assistant", "content": content_blocks})
        tool_blocks = [block for block in content_blocks if block.get("type") == "tool_use"]
        if not tool_blocks:
            _chat_history.append({"role": "assistant", "content": full_text})
            return

        tool_results = []
        repeated_tool_name = ""
        for block in tool_blocks:
            fn_name = block["name"]
            fn_args = block["input"] if isinstance(block.get("input"), dict) else {}
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

            tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": result})
            if state.record_signature(fn_name, fn_args) and not repeated_tool_name:
                repeated_tool_name = fn_name

        api_msgs.append({"role": "user", "content": tool_results})
        if repeated_tool_name:
            reply = _repeated_tool_reply(_agent, repeated_tool_name)
            _chat_history.append({"role": "assistant", "content": reply})
            yield _token_event(reply)
            return

    reply = full_text_all or _max_rounds_reply()
    if full_text_all:
        reply = _max_rounds_reply()
        yield _token_event(reply)
    _chat_history.append({"role": "assistant", "content": reply})
```

- [ ] **Step 2: Run backend tests**

Run:

```bash
pytest tests/test_web_streaming.py -q
```

Expected: PASS.

- [ ] **Step 3: Run the focused Canvas and WeChat tests**

Run:

```bash
pytest tests/test_canvas_tools.py tests/test_wechat_progress.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit Anthropic parity**

```bash
git add sjtu_agent/web/server.py
git commit -m "feat: align anthropic web tool progress"
```

---

### Task 5: Frontend Timeline Tests

**Files:**
- Create: `tests/test_web_static.py`
- Test: `tests/test_web_static.py`

- [ ] **Step 1: Write failing static frontend tests**

Create `tests/test_web_static.py` with this content:

```python
from __future__ import annotations

from pathlib import Path


HTML = Path("sjtu_agent/web/static/index.html").read_text(encoding="utf-8")


def test_web_chat_has_canvas_tool_labels():
    for name in [
        "list_canvas_courses",
        "get_canvas_course_announcements",
        "get_canvas_course_quizzes",
        "get_canvas_course_updates",
        "get_canvas_overview",
        "get_canvas_todo",
        "configure_canvas_monitor",
        "list_canvas_assignments",
        "submit_canvas_assignment",
        "setup_canvas",
    ]:
        assert name in HTML


def test_web_chat_uses_keyed_tool_cards():
    assert "const toolCards = new Map();" in HTML
    assert "toolCards.set(toolId, card);" in HTML
    assert "toolCards.get(toolId)" in HTML
    assert "currentToolCard" not in HTML


def test_web_chat_renders_progress_events():
    for helper in [
        "appendStatusRow",
        "appendToolCard",
        "markToolDone",
        "appendRetryRow",
        "appendLimitRow",
    ]:
        assert f"function {helper}" in HTML


def test_web_chat_handles_structured_done_event():
    assert "evt.done" in HTML
    assert "payload === '[DONE]'" in HTML
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_web_static.py -q
```

Expected: FAIL because the current frontend still uses `currentToolCard` and lacks the new helpers.

- [ ] **Step 3: Commit failing frontend tests**

```bash
git add tests/test_web_static.py
git commit -m "test: cover web chat progress timeline"
```

---

### Task 6: Frontend Progress Timeline Implementation

**Files:**
- Modify: `sjtu_agent/web/static/index.html`
- Test: `tests/test_web_static.py`

- [ ] **Step 1: Add timeline CSS**

In `sjtu_agent/web/static/index.html`, replace the `.tool-card` CSS block from `/* 工具调用卡片 */` through `.tool-card .tool-label` with:

```css
    /* 工具调用时间线 */
    .progress-row,
    .tool-card {
      align-self: flex-start;
      max-width: calc(100% - 50px);
      margin-left: 42px;
      background: var(--surface2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 9px 12px;
      font-size: 13px;
      color: var(--text2);
    }
    .progress-row {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .progress-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--text3);
      flex-shrink: 0;
    }
    .progress-row.running .progress-dot {
      width: 14px;
      height: 14px;
      border: 2px solid var(--text3);
      border-right-color: transparent;
      background: transparent;
      animation: spin .6s linear infinite;
    }
    .progress-row.done .progress-dot { background: var(--green); }
    .progress-row.warn .progress-dot { background: var(--yellow); }
    .progress-row.error .progress-dot { background: var(--red); }
    .progress-text {
      color: var(--text2);
      line-height: 1.45;
    }
    .tool-card .tool-head {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      user-select: none;
    }
    .tool-card .tool-icon {
      width: 14px; height: 14px;
      border: 2px solid var(--text3);
      border-right-color: transparent;
      border-radius: 50%;
      animation: spin .6s linear infinite;
      flex-shrink: 0;
    }
    .tool-card.done .tool-icon {
      border: none;
      animation: none;
      background: var(--green);
      width: 14px; height: 14px;
      position: relative;
    }
    .tool-card.done .tool-icon::after {
      content: "✓";
      color: #0a0b10;
      font-size: 10px;
      font-weight: 700;
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .tool-card.error .tool-icon {
      border: none;
      animation: none;
      background: var(--red);
    }
    .tool-card.limited .tool-icon {
      border: none;
      animation: none;
      background: var(--yellow);
    }
    .tool-card .tool-name {
      font-family: "SF Mono", "JetBrains Mono", Menlo, monospace;
      color: var(--text);
      font-size: 12.5px;
    }
    .tool-card .tool-summary {
      color: var(--text3);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .tool-card .tool-caret {
      margin-left: auto;
      color: var(--text3);
      font-size: 11px;
      transition: transform .15s;
    }
    .tool-card.expanded .tool-caret { transform: rotate(90deg); }
    .tool-card .tool-body {
      display: none;
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px dashed var(--border);
    }
    .tool-card.expanded .tool-body { display: block; }
    .tool-card pre {
      background: #0a0b10;
      border-radius: 6px;
      padding: 8px 10px;
      margin: 4px 0;
      overflow-x: auto;
      font-family: "SF Mono", "JetBrains Mono", Menlo, monospace;
      font-size: 11.5px;
      line-height: 1.5;
      color: var(--text2);
      max-height: 240px;
      overflow-y: auto;
    }
    .tool-card .tool-label {
      color: var(--text3);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .5px;
      margin: 6px 0 2px;
    }
```

- [ ] **Step 2: Replace tool label and progress helper JavaScript**

Replace the `TOOL_LABELS` object and `appendToolCard` function with:

```javascript
const TOOL_LABELS = {
  get_ddls: '获取作业 DDL',
  get_all: '获取全部信息',
  get_next_lab: '查询物理实验安排',
  get_schedule: '查询课表',
  query_grades: '查询成绩',
  check_setup: '检查配置',
  download_assignments: '下载作业材料',
  search_campus: '搜索校园内容',
  browse_mysjtu: '浏览交大门户',
  list_reminders: '查看提醒',
  read_emails: '读取邮件',
  send_email: '发送邮件',
  setup_canvas: '引导配置 Canvas',
  list_canvas_courses: '读取 Canvas 课程',
  get_canvas_course_announcements: '读取 Canvas 公告',
  get_canvas_course_quizzes: '读取 Canvas Quiz',
  get_canvas_course_updates: '汇总 Canvas 课程动态',
  get_canvas_overview: '汇总 Canvas 总览',
  get_canvas_todo: '读取 Canvas 待办',
  configure_canvas_monitor: '配置 Canvas 监控',
  list_canvas_assignments: '列出 Canvas 作业',
  submit_canvas_assignment: '上传并提交作业',
};

function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function formatJson(value) {
  if (value === undefined || value === null || value === '') return '(无)';
  if (typeof value === 'string') {
    try { return JSON.stringify(JSON.parse(value), null, 2); }
    catch { return value; }
  }
  return JSON.stringify(value, null, 2);
}

function appendStatusRow(text, kind = 'running') {
  const msgs = document.getElementById('chat-messages');
  const row = document.createElement('div');
  row.className = 'progress-row ' + kind;
  row.innerHTML = `
    <span class="progress-dot"></span>
    <span class="progress-text">${escapeHtml(text)}</span>
  `;
  msgs.appendChild(row);
  msgs.scrollTop = msgs.scrollHeight;
  return row;
}

function appendRetryRow(retry) {
  const msg = retry.message || `模型服务忙，${retry.delay_s || 0}s 后自动重试（第 ${retry.attempt || 1} 次）…`;
  return appendStatusRow(msg, 'warn');
}

function appendLimitRow(limit) {
  const msg = limit.message || `工具调用已到上限（${limit.max_tool_calls || '?'} 次），正在整理已有结果…`;
  return appendStatusRow(msg, 'warn');
}

function appendToolCard(tool) {
  const msgs = document.getElementById('chat-messages');
  const card = document.createElement('div');
  const label = tool.label || TOOL_LABELS[tool.name] || tool.name;
  const summary = tool.summary ? `：${tool.summary}` : '';
  card.className = 'tool-card running';
  card.dataset.toolId = tool.id;
  card.innerHTML = `
    <div class="tool-head" onclick="this.parentElement.classList.toggle('expanded')">
      <div class="tool-icon"></div>
      <span class="tool-name">#${tool.index || '?'} ${escapeHtml(label)}</span>
      <span class="tool-summary">${escapeHtml(summary)}</span>
      <span class="tool-caret">▶</span>
    </div>
    <div class="tool-body">
      <div class="tool-label">工具</div>
      <pre>${escapeHtml(tool.name || '')}</pre>
      <div class="tool-label">参数</div>
      <pre>${escapeHtml(formatJson(tool.input))}</pre>
      <div class="tool-label">结果</div>
      <pre class="tool-result">等待中…</pre>
    </div>
  `;
  msgs.appendChild(card);
  msgs.scrollTop = msgs.scrollHeight;
  return card;
}

function markToolDone(card, toolEnd) {
  card.classList.remove('running');
  card.classList.add(toolEnd.error ? 'error' : 'done');
  const result = card.querySelector('.tool-result');
  if (result) {
    const preview = toolEnd.result_preview ?? toolEnd.result ?? toolEnd.error ?? '';
    result.textContent = formatJson(preview);
  }
  const name = card.querySelector('.tool-name');
  if (name && toolEnd.elapsed_ms !== undefined) {
    const seconds = (Number(toolEnd.elapsed_ms || 0) / 1000).toFixed(1);
    name.textContent = `${name.textContent}（${seconds}s）`;
  }
}
```

- [ ] **Step 3: Replace tool event handling in `sendChat`**

Inside `sendChat`, replace:

```javascript
  let currentToolCard = null;
```

with:

```javascript
  const toolCards = new Map();
  let generatedToolId = 0;
  let streamDone = false;
```

Then replace the event handling block from `if (evt.error) {` through the closing `if (evt.tool_end)` block with:

```javascript
        if (evt.done) {
          streamDone = true;
          break;
        }

        if (evt.error) {
          appendStatusRow(evt.error, 'error');
          bubble.innerHTML = '<p style="color:var(--red)">❌ ' + escapeHtml(evt.error) + '</p>';
          streamDone = true;
          break;
        }

        if (evt.status) {
          appendStatusRow(evt.status.message || evt.status.phase || '正在处理…', evt.status.phase === 'done' ? 'done' : 'running');
        }

        if (evt.retry) {
          appendRetryRow(evt.retry);
        }

        if (evt.tool_limit) {
          appendLimitRow(evt.tool_limit);
        }

        if (evt.token) {
          if (firstToken) { fullText = ''; firstToken = false; }
          fullText += evt.token;
          bubble.innerHTML = renderMd(fullText) + '<span class="cursor"></span>';
          document.getElementById('chat-messages').scrollTop =
            document.getElementById('chat-messages').scrollHeight;
        }

        if (evt.tool_start) {
          const tool = evt.tool_start;
          const toolId = tool.id || `tool-local-${++generatedToolId}`;
          tool.id = toolId;
          const card = appendToolCard(tool);
          toolCards.set(toolId, card);
        }

        if (evt.tool_end) {
          const toolEnd = evt.tool_end;
          const toolId = toolEnd.id || `tool-local-${++generatedToolId}`;
          const card = toolCards.get(toolId);
          if (card) {
            markToolDone(card, toolEnd);
          }
        }
```

After the inner `for (const line of lines)` loop, add:

```javascript
      if (streamDone) break;
```

- [ ] **Step 4: Run frontend static tests**

Run:

```bash
pytest tests/test_web_static.py -q
```

Expected: PASS.

- [ ] **Step 5: Run a JavaScript syntax check**

Extract and check the inline script:

```bash
python - <<'PY'
from pathlib import Path
html = Path("sjtu_agent/web/static/index.html").read_text(encoding="utf-8")
start = html.index("<script>") + len("<script>")
end = html.rindex("</script>")
Path("/tmp/sjtu-agent-web-inline.js").write_text(html[start:end], encoding="utf-8")
PY
node --check /tmp/sjtu-agent-web-inline.js
```

Expected: `node --check` exits 0.

- [ ] **Step 6: Commit frontend timeline**

```bash
git add sjtu_agent/web/static/index.html tests/test_web_static.py
git commit -m "feat: show web chat tool progress timeline"
```

---

### Task 7: Final Verification and Runtime Check

**Files:**
- Modify: none unless verification exposes a bug
- Test: all focused tests and local web UI

- [ ] **Step 1: Run focused regression tests**

Run:

```bash
pytest tests/test_web_streaming.py tests/test_web_static.py tests/test_wechat_progress.py tests/test_canvas_tools.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the full test suite**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 3: Start the web server locally**

Run:

```bash
python -m sjtu_agent.cli web --port 8765 --no-browser
```

Expected: terminal prints a local URL for `127.0.0.1:8765` and stays running.

- [ ] **Step 4: Manually verify web streaming behavior**

Open `http://127.0.0.1:8765`, go to the chat page, and send:

```text
看看 Canvas 总览，尤其是 quiz 和最近通知
```

Expected:

- The assistant bubble appears immediately.
- A compact progress row says `正在思考…`.
- Canvas tool rows appear with Chinese labels.
- Tool rows can be expanded to show tool name, parameters, and result preview.
- The final answer streams token by token.
- The page does not remain stuck at `正在思考…` or `等待中`.

- [ ] **Step 5: Stop the web server**

Press `Ctrl-C` in the server terminal.

Expected: the server exits cleanly.

- [ ] **Step 6: Commit any verification fixes**

If Step 1 through Step 5 required code changes, commit only those files:

```bash
git add sjtu_agent/web/server.py sjtu_agent/web/static/index.html tests/test_web_streaming.py tests/test_web_static.py
git commit -m "fix: polish web agent progress streaming"
```

If no code changes were required, do not create an empty commit.

---

## Implementation Notes

- Keep the current SSE `data: <json payload>\n\n` format so existing parsing continues to work.
- Emit `{"done": true}` instead of only `data: [DONE]`. The frontend should still tolerate `data: [DONE]` for compatibility.
- Do not show hidden model chain-of-thought. Only show public text, status messages, tool names, arguments, result previews, timings, limits, retries, and errors.
- Use Chinese user-facing messages for progress and recovery states.
- The backend lock is process-local by design because this web UI is a local personal server.
- Stage carefully. Existing unrelated files are dirty in this worktree.
