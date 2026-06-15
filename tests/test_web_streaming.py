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
