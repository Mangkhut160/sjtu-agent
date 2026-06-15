from __future__ import annotations

import json
import threading
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


def _anthropic_tool_start(name: str, block_id: str):
    return SimpleNamespace(
        type="content_block_start",
        content_block=SimpleNamespace(type="tool_use", id=block_id, name=name),
    )


def _anthropic_text_start():
    return SimpleNamespace(
        type="content_block_start",
        content_block=SimpleNamespace(type="text"),
    )


def _anthropic_input_delta(partial_json: str, index: int = 0):
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="input_json_delta", partial_json=partial_json),
    )


def _anthropic_text_delta(text: str):
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="text_delta", text=text),
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


def _event_from_chunk(chunk: str):
    block = chunk.strip()
    assert block.startswith("data: ")
    payload = block.removeprefix("data: ").strip()
    if payload == "[DONE]":
        return {"done": True}
    return json.loads(payload)


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


class PartialThenFailingStream:
    def __iter__(self):
        yield _chunk(content="先说一段。")
        raise RuntimeError("Concurrency limit exceeded for user, please retry later")


class MultiChunkThenFailingStream:
    def __iter__(self):
        yield _chunk(content="第一段")
        yield _chunk(content="第二段")
        raise RuntimeError("Concurrency limit exceeded for user, please retry later")


class ObservableOpenAIStream:
    def __init__(self):
        self.finished = False

    def __iter__(self):
        yield _chunk(content="第一段")
        yield _chunk(content="第二段")
        self.finished = True


class FakeOpenAIRealtimeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.stream = ObservableOpenAIStream()

    def create(self, **_kwargs):
        return self.stream


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


class FakePartialThenTransientClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return PartialThenFailingStream()
        return [_chunk(content="成功回复。")]


class FakeEmittedPartialThenTransientClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return MultiChunkThenFailingStream()
        return [_chunk(content="成功回复。")]


class FakePersistentVisiblePartialLimitClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return MultiChunkThenFailingStream()


class FakeAnthropicStream:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self.events)


class FailingAnthropicStream:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        if False:
            yield None
        raise RuntimeError("Concurrency limit exceeded for user, please retry later")


class FakeAnthropicToolClient:
    def __init__(self):
        self.messages = SimpleNamespace(stream=self.stream)
        self.calls = 0

    def stream(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return FakeAnthropicStream([
                _anthropic_tool_start("get_canvas_todo", "toolu_1"),
                _anthropic_input_delta('{"limit": 1}', index=0),
                _anthropic_tool_start("get_canvas_course_quizzes", "toolu_2"),
                _anthropic_input_delta('{"course": "ECE2300"}', index=1),
            ])
        return FakeAnthropicStream([
            _anthropic_text_start(),
            _anthropic_text_delta("查好了。"),
        ])


class FakeAnthropicTransientLimitClient:
    def __init__(self):
        self.messages = SimpleNamespace(stream=self.stream)
        self.calls = 0

    def stream(self, **_kwargs):
        self.calls += 1
        if self.calls < 3:
            return FailingAnthropicStream()
        return FakeAnthropicStream([
            _anthropic_text_start(),
            _anthropic_text_delta("现在可以继续了。"),
        ])


class FakeAnthropicRotatingClient:
    def __init__(self):
        self.messages = SimpleNamespace(stream=self.stream)
        self.calls = 0

    def stream(self, **_kwargs):
        self.calls += 1
        return FakeAnthropicStream([
            _anthropic_tool_start("get_canvas_course_updates", f"toolu_{self.calls}"),
            _anthropic_input_delta(
                json.dumps({"course": f"COURSE{self.calls}"}, ensure_ascii=False),
                index=0,
            ),
        ])


def _reset_web_server(monkeypatch, client, proto: str = "openai"):
    import agent
    import sjtu_agent.web.server as server

    monkeypatch.setattr(server, "_chat_history", [])
    monkeypatch.setattr(server, "_chat_lock", threading.Lock(), raising=False)
    model = "claude-sonnet-4-5" if proto == "anthropic" else "deepseek-chat"
    monkeypatch.setattr(server, "_get_chat_client", lambda: (client, model, proto))
    monkeypatch.setattr(agent, "run_tool", lambda name, args: '{"ok": true, "tool": "%s"}' % name)
    monkeypatch.setattr(agent, "_anthropic_tools", lambda: [], raising=False)
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
    assert [end["index"] for end in ends] == [1, 2]
    assert [end["name"] for end in ends] == [
        "get_canvas_todo",
        "get_canvas_course_quizzes",
    ]
    assert [end["label"] for end in ends] == [
        "正在读取 Canvas 待办",
        "正在读取 Canvas Quiz",
    ]
    assert all(
        isinstance(end["elapsed_ms"], int) and end["elapsed_ms"] >= 0
        for end in ends
    )
    assert all(end["result"] == end["result_preview"] for end in ends)
    assert all('"ok": true' in end["result_preview"] for end in ends)
    assert tokens == ["查好了。"]
    assert events[-1] == {"done": True}


def test_web_stream_chat_openai_yields_first_token_before_stream_finishes(monkeypatch):
    client = FakeOpenAIRealtimeClient()
    server = _reset_web_server(monkeypatch, client)

    chunks = server._stream_chat("讲一个长一点的回复")
    try:
        assert _event_from_chunk(next(chunks))["status"]["phase"] == "thinking"
        first_token = _event_from_chunk(next(chunks))
        assert first_token == {"token": "第一段"}
        assert client.stream.finished is False
    finally:
        chunks.close()


def test_web_stream_chat_preserves_full_wechat_qr_tool_result(monkeypatch):
    import agent

    server = _reset_web_server(monkeypatch, FakeOpenAIToolClient())
    qr_payload = {
        "qr_base64": "x" * 900,
        "qrcode_key": "qr-key-123",
        "ilink_base": "https://ilinkai.weixin.qq.com",
    }
    full_result = json.dumps(qr_payload, ensure_ascii=False)
    monkeypatch.setattr(agent, "run_tool", lambda _name, _args: full_result)

    events = _events_from(server._stream_chat("配置微信"))

    tool_end = next(event["tool_end"] for event in events if "tool_end" in event)
    assert "result_preview" in tool_end
    assert "result" in tool_end
    assert len(tool_end["result_preview"]) < len(tool_end["result"])
    assert json.loads(tool_end["result"]) == qr_payload


def test_web_stream_chat_anthropic_reports_keyed_tool_progress(monkeypatch):
    server = _reset_web_server(monkeypatch, FakeAnthropicToolClient(), proto="anthropic")

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
    assert [end["index"] for end in ends] == [1, 2]
    assert [end["name"] for end in ends] == [
        "get_canvas_todo",
        "get_canvas_course_quizzes",
    ]
    assert [end["label"] for end in ends] == [
        "正在读取 Canvas 待办",
        "正在读取 Canvas Quiz",
    ]
    assert all(
        isinstance(end["elapsed_ms"], int) and end["elapsed_ms"] >= 0
        for end in ends
    )
    assert all(end["result"] == end["result_preview"] for end in ends)
    assert all('"ok": true' in end["result_preview"] for end in ends)
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


def test_web_stream_chat_emits_max_rounds_fallback_without_streamed_text(monkeypatch):
    client = FakeOpenAIRotatingClient()
    server = _reset_web_server(monkeypatch, client)
    monkeypatch.setattr(server, "MAX_TOOL_ROUNDS", 3, raising=False)

    events = _events_from(server._stream_chat("持续查询 Canvas 动态"))

    starts = [event for event in events if "tool_start" in event]
    tokens = "".join(event.get("token", "") for event in events)

    assert client.calls == 3
    assert len(starts) == 3
    assert server._max_rounds_reply() in tokens
    assert server._chat_history[-1] == {
        "role": "assistant",
        "content": server._max_rounds_reply(),
    }
    assert events[-1] == {"done": True}


def test_web_stream_chat_anthropic_emits_max_rounds_fallback_without_streamed_text(monkeypatch):
    client = FakeAnthropicRotatingClient()
    server = _reset_web_server(monkeypatch, client, proto="anthropic")
    monkeypatch.setattr(server, "MAX_TOOL_ROUNDS", 3, raising=False)

    events = _events_from(server._stream_chat("持续查询 Canvas 动态"))

    starts = [event for event in events if "tool_start" in event]
    tokens = "".join(event.get("token", "") for event in events)

    assert client.calls == 3
    assert len(starts) == 3
    assert server._max_rounds_reply() in tokens
    assert server._chat_history[-1] == {
        "role": "assistant",
        "content": server._max_rounds_reply(),
    }
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


def test_web_stream_chat_anthropic_retries_transient_concurrency_limit(monkeypatch):
    client = FakeAnthropicTransientLimitClient()
    server = _reset_web_server(monkeypatch, client, proto="anthropic")
    monkeypatch.setattr(server, "_LLM_TRANSIENT_RETRY_DELAYS", (0, 0), raising=False)

    events = _events_from(server._stream_chat("继续"))

    retries = [event["retry"] for event in events if "retry" in event]
    tokens = [event["token"] for event in events if "token" in event]

    assert client.calls == 3
    assert [retry["attempt"] for retry in retries] == [1, 2]
    assert tokens == ["现在可以继续了。"]
    assert events[-1] == {"done": True}


def test_web_stream_chat_discards_partial_tokens_from_failed_retry_attempt(monkeypatch):
    client = FakePartialThenTransientClient()
    server = _reset_web_server(monkeypatch, client)
    monkeypatch.setattr(server, "_LLM_TRANSIENT_RETRY_DELAYS", (0,), raising=False)

    events = _events_from(server._stream_chat("继续"))

    retries = [event["retry"] for event in events if "retry" in event]
    tokens = [event["token"] for event in events if "token" in event]

    assert client.calls == 2
    assert [retry["attempt"] for retry in retries] == [1]
    assert tokens == ["成功回复。"]
    assert events[-1] == {"done": True}


def test_web_stream_chat_marks_retry_reset_when_visible_partial_text_was_sent(monkeypatch):
    client = FakeEmittedPartialThenTransientClient()
    server = _reset_web_server(monkeypatch, client)
    monkeypatch.setattr(server, "_LLM_TRANSIENT_RETRY_DELAYS", (0,), raising=False)

    events = _events_from(server._stream_chat("继续"))

    retries = [event["retry"] for event in events if "retry" in event]
    tokens = [event["token"] for event in events if "token" in event]

    assert client.calls == 2
    assert retries[0]["reset_text"] is True
    assert tokens == ["第一段", "成功回复。"]
    assert events[-1] == {"done": True}


def test_web_stream_chat_resets_visible_partial_text_before_final_busy_reply(monkeypatch):
    client = FakePersistentVisiblePartialLimitClient()
    server = _reset_web_server(monkeypatch, client)
    monkeypatch.setattr(server, "_LLM_TRANSIENT_RETRY_DELAYS", (0,), raising=False)

    events = _events_from(server._stream_chat("继续"))

    retries = [event["retry"] for event in events if "retry" in event]
    tokens = [event["token"] for event in events if "token" in event]

    assert client.calls == 2
    assert [retry.get("reset_text") for retry in retries] == [True, True]
    assert tokens == ["第一段", "第一段", server._llm_busy_reply()]
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


def test_web_chat_clear_rejects_while_turn_is_active(monkeypatch):
    server = _reset_web_server(monkeypatch, FakeOpenAIToolClient())
    existing_history = [{"role": "user", "content": "保留这轮对话"}]
    monkeypatch.setattr(server, "_chat_history", existing_history)
    responses = []
    handler = SimpleNamespace(
        path="/api/chat/clear",
        _send_json=lambda data, status=200: responses.append((status, data)),
    )

    server._chat_lock.acquire()
    try:
        server._Handler.do_POST(handler)
    finally:
        server._chat_lock.release()

    assert responses == [(
        409,
        {"ok": False, "error": "上一轮对话还在运行，请等它结束后再清空。"},
    )]
    assert server._chat_history == existing_history
