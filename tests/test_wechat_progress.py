from __future__ import annotations

from types import SimpleNamespace


def _chunk(*, content: str = "", tool_calls=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tool_call(name: str, arguments: str = "{}"):
    return SimpleNamespace(
        index=0,
        id="call_1",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return [
                _chunk(
                    tool_calls=[
                        _tool_call("get_canvas_todo", '{"limit": 1}'),
                    ],
                )
            ]
        return [_chunk(content="查好了。")]


class FakeThinkingOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **_kwargs):
        return [
            _chunk(content="<think>hidden reasoning</think>"),
            _chunk(content="公开回答。"),
        ]


class FakeLoopingOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **_kwargs):
        return [
            _chunk(
                tool_calls=[
                    _tool_call("get_canvas_course_quizzes", '{"course": "电磁学"}'),
                ],
            )
        ]


class FakeMultiToolLoopingOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **_kwargs):
        return [
            _chunk(
                tool_calls=[
                    _tool_call("get_canvas_course_quizzes", '{"course": "电磁学"}'),
                    SimpleNamespace(
                        index=1,
                        id="call_2",
                        function=SimpleNamespace(name="get_canvas_todo", arguments='{"limit": 1}'),
                    ),
                ],
            )
        ]


class FakeRotatingCourseUpdatesClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return [
            _chunk(
                tool_calls=[
                    _tool_call(
                        "get_canvas_course_updates",
                        f'{{"course": "COURSE{self.calls}", "include": ["announcements", "quizzes"]}}',
                    ),
                ],
            )
        ]


class FailingStream:
    def __iter__(self):
        if False:
            yield None
        raise RuntimeError("Concurrency limit exceeded for user, please retry later")


class FakeTransientLimitOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls < 3:
            return FailingStream()
        return [_chunk(content="现在可以继续了。")]


class FakePersistentLimitOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return FailingStream()


def test_wechat_streamed_turn_reports_openai_tool_progress(monkeypatch):
    import scripts.wechat_bot as wechat

    events: list[tuple[str, dict]] = []
    sess = {
        "messages": [],
        "model_box": ["deepseek-chat"],
        "client_box": [FakeOpenAIClient()],
    }

    monkeypatch.setattr(wechat.agent, "_is_anthropic_model", lambda _model: False)
    monkeypatch.setattr(wechat.agent, "run_tool", lambda name, args: '{"ok": true}')

    reply = wechat._streamed_turn(
        sess,
        "看看 Canvas 待办",
        lambda event_type, payload: events.append((event_type, payload)),
    )

    assert reply == "查好了。"
    assert events[0][0] == "tool_start"
    assert events[0][1]["name"] == "get_canvas_todo"
    assert events[0][1]["index"] == 1
    assert events[1][0] == "tool_end"
    assert events[1][1]["name"] == "get_canvas_todo"
    assert events[1][1]["index"] == 1
    assert isinstance(events[1][1]["elapsed_ms"], int)
    assert events[2] == ("first_token", {})


def test_wechat_streamed_turn_does_not_expose_think_tags(monkeypatch):
    import scripts.wechat_bot as wechat

    sess = {
        "messages": [],
        "model_box": ["deepseek-chat"],
        "client_box": [FakeThinkingOpenAIClient()],
    }

    monkeypatch.setattr(wechat.agent, "_is_anthropic_model", lambda _model: False)

    reply = wechat._streamed_turn(sess, "解释一下", lambda *_args: None)

    assert reply == "公开回答。"
    assert "hidden reasoning" not in reply
    assert "<think>" not in reply


def test_wechat_streamed_turn_stops_repeated_tool_loop(monkeypatch):
    import scripts.wechat_bot as wechat

    events: list[tuple[str, dict]] = []
    sess = {
        "messages": [],
        "model_box": ["deepseek-chat"],
        "client_box": [FakeLoopingOpenAIClient()],
    }

    monkeypatch.setattr(wechat.agent, "_is_anthropic_model", lambda _model: False)
    monkeypatch.setattr(wechat.agent, "run_tool", lambda name, args: '{"ok": false, "error": "course_not_found"}')

    reply = wechat._streamed_turn(
        sess,
        "帮我看看电磁学的quiz",
        lambda event_type, payload: events.append((event_type, payload)),
        max_rounds=8,
    )

    assert "连续调用" in reply
    assert "Canvas Quiz" in reply
    assert [event for event, _payload in events].count("tool_start") == 3
    assert [event for event, _payload in events].count("tool_end") == 3


def test_wechat_streamed_turn_finishes_peer_tools_before_loop_stop(monkeypatch):
    import scripts.wechat_bot as wechat

    sess = {
        "messages": [],
        "model_box": ["deepseek-chat"],
        "client_box": [FakeMultiToolLoopingOpenAIClient()],
    }

    monkeypatch.setattr(wechat.agent, "_is_anthropic_model", lambda _model: False)
    monkeypatch.setattr(wechat.agent, "run_tool", lambda name, args: '{"ok": false}')

    wechat._streamed_turn(sess, "帮我看看电磁学的quiz和todo", lambda *_args: None, max_rounds=8)

    last_tool_call_idx = next(
        i for i in range(len(sess["messages"]) - 1, -1, -1)
        if sess["messages"][i].get("role") == "assistant" and sess["messages"][i].get("tool_calls")
    )
    last_tool_call_msg = sess["messages"][last_tool_call_idx]
    following = sess["messages"][last_tool_call_idx + 1 :]
    tool_ids = [tc["id"] for tc in last_tool_call_msg["tool_calls"]]
    answered_ids = [m.get("tool_call_id") for m in following if m.get("role") == "tool"]

    assert tool_ids == ["call_1", "call_2"]
    assert answered_ids[:2] == tool_ids


def test_wechat_streamed_turn_stops_after_total_tool_budget(monkeypatch):
    import scripts.wechat_bot as wechat

    events: list[tuple[str, dict]] = []
    executed: list[tuple[str, dict]] = []
    sess = {
        "messages": [],
        "model_box": ["deepseek-chat"],
        "client_box": [FakeRotatingCourseUpdatesClient()],
    }

    monkeypatch.setattr(wechat.agent, "_is_anthropic_model", lambda _model: False)
    monkeypatch.setattr(
        wechat.agent,
        "run_tool",
        lambda name, args: executed.append((name, args)) or '{"ok": true}',
    )

    reply = wechat._streamed_turn(
        sess,
        "帮我看看 quiz 和最新通知，还有最近任务",
        lambda event_type, payload: events.append((event_type, payload)),
        max_rounds=8,
        max_tool_calls=3,
    )

    assert len(executed) == 3
    assert "工具调用次数已经达到上限" in reply
    assert "get_canvas_overview" in reply
    assert events[-1][0] == "tool_limit"
    assert events[-1][1]["max_tool_calls"] == 3


def test_wechat_system_context_includes_canvas_course_index(monkeypatch):
    import scripts.wechat_bot as wechat

    monkeypatch.setattr(
        wechat,
        "_get_canvas_course_index",
        lambda: [
            {"course_id": 92355, "name": "ECE2300JSU2026-1", "course_code": "ECE2300JSU2026-1"},
            {"course_id": 92353, "name": "ECE2700JSU2026", "course_code": "ECE2700JSU2026"},
        ],
    )

    context = wechat._build_system_ctx()

    assert "当前 Canvas 课程索引" in context
    assert "92355 | ECE2300JSU2026-1 | ECE2300JSU2026-1" in context
    assert "用户说“230这门课”时" in context


def test_wechat_progress_sender_sends_visible_status_messages():
    import scripts.wechat_bot as wechat

    sent: list[str] = []

    class FakeClient:
        def send(self, text: str, to_user_id: str = "", context_token: str = ""):
            sent.append(text)
            assert to_user_id == "user-1"
            assert context_token == "ctx-1"

    on_progress = wechat._make_progress_sender(FakeClient(), "user-1", "ctx-1")

    on_progress("thinking", {})
    on_progress("tool_start", {"name": "get_canvas_todo", "index": 2, "summary": "limit=3"})
    on_progress("tool_end", {"name": "get_canvas_todo", "index": 2, "elapsed_ms": 1234})
    on_progress("retry", {"attempt": 1, "delay_s": 2})
    on_progress("tool_limit", {"max_tool_calls": 12})
    on_progress("first_token", {})

    assert sent == [
        "⏳ 正在思考…",
        "⚙️ #2 正在读取 Canvas 待办：limit=3…",
        "✅ #2 正在读取 Canvas 待办（1.2s）",
        "⏳ 模型服务忙，2s 后自动重试（第 1 次）…",
        "⏹️ 工具调用已到上限（12 次），正在整理已有结果…",
        "💬 开始生成回复…",
    ]


def test_wechat_streamed_turn_retries_transient_concurrency_limit(monkeypatch):
    import scripts.wechat_bot as wechat

    events: list[tuple[str, dict]] = []
    client = FakeTransientLimitOpenAIClient()
    sess = {
        "messages": [],
        "model_box": ["deepseek-chat"],
        "client_box": [client],
    }

    monkeypatch.setattr(wechat.agent, "_is_anthropic_model", lambda _model: False)
    monkeypatch.setattr(wechat, "_LLM_TRANSIENT_RETRY_DELAYS", (0, 0), raising=False)

    reply = wechat._streamed_turn(
        sess,
        "继续",
        lambda event_type, payload: events.append((event_type, payload)),
    )

    assert reply == "现在可以继续了。"
    assert client.calls == 3
    assert [event for event, _payload in events] == ["retry", "retry", "first_token"]


def test_wechat_streamed_turn_returns_friendly_message_when_concurrency_limit_persists(monkeypatch):
    import scripts.wechat_bot as wechat

    client = FakePersistentLimitOpenAIClient()
    sess = {
        "messages": [],
        "model_box": ["deepseek-chat"],
        "client_box": [client],
    }

    monkeypatch.setattr(wechat.agent, "_is_anthropic_model", lambda _model: False)
    monkeypatch.setattr(wechat, "_LLM_TRANSIENT_RETRY_DELAYS", (0, 0), raising=False)

    reply = wechat._streamed_turn(sess, "继续", lambda *_args: None)

    assert client.calls == 3
    assert "模型服务现在有点忙" in reply
    assert "Concurrency limit" not in reply
    assert sess["messages"][-1] == {"role": "assistant", "content": reply}
