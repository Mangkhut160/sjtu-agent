# Web Agent Streaming Tool Progress Design

Date: 2026-06-15

## Context

The web agent already streams assistant text through `POST /api/chat` with
`text/event-stream`. The current stuck-looking behavior comes from weaker tool
loop control and UI state handling, not from a lack of streaming.

Current findings:

- `sjtu_agent/web/server.py` streams `{token}`, `{tool_start}`, `{tool_end}`,
  and `{error}` events.
- The web backend has `MAX_TOOL_ROUNDS = 20`, but no total tool-call budget,
  repeated-call guard, or transient model retry handling.
- The frontend tracks tool calls with a single `currentToolCard`, which can
  mismatch when a model emits multiple tool calls in one turn.
- The frontend tool label map is missing many Canvas tools, so users see raw
  function names for important operations.
- The web server is threaded while chat history is global, so concurrent chat
  requests can overlap and make upstream concurrency errors more likely.

The desired UX is option C: show a concise ChatGPT-style progress summary by
default, with a way to expand and inspect full tool parameters and results.

## Goals

- Keep assistant text streaming token by token.
- Make every long-running step visible to the user.
- Show concise progress by default and complete tool details on demand.
- Stop runaway tool loops before the user sees an endless "thinking" state.
- Translate transient upstream errors into useful Chinese status and recovery
  messages.
- Keep this change scoped to the web agent path.

## Non-Goals

- Do not refactor CLI, WeChat, and web into one shared runner in this change.
- Do not change Canvas tool behavior beyond labels, summaries, and progress
  visibility.
- Do not expose hidden model chain-of-thought. Tool calls, tool arguments,
  timings, retries, and public status should be visible; private reasoning
  should not be surfaced.

## Backend Design

`POST /api/chat` remains an SSE endpoint. It will emit a more structured event
stream while preserving compatibility with existing `{token: "..."}` events.

Event examples:

```json
{"status":{"phase":"thinking","message":"正在思考…"}}
{"tool_start":{"id":"tool-1","index":1,"name":"get_canvas_course_quizzes","label":"正在读取 Canvas Quiz","summary":"course=ECE2300"}}
{"tool_end":{"id":"tool-1","index":1,"name":"get_canvas_course_quizzes","elapsed_ms":1234,"result_preview":"..."}}
{"retry":{"attempt":1,"delay_s":2,"message":"模型服务忙，2s 后自动重试…"}}
{"tool_limit":{"max_tool_calls":12,"message":"工具调用已到上限，正在整理已有结果…"}}
{"token":"最终回答的一部分"}
{"done":true}
```

The backend should add these controls to both OpenAI-style and Anthropic-style
streaming paths:

- Total tool-call budget: default 12 tool calls per user turn.
- Repeated-call guard: if the same tool name and normalized arguments are called
  3 times in one turn, stop the loop and ask the user to narrow the request or
  provide a course code / Canvas course name.
- Max-round guard: if model rounds are exhausted, return a friendly stop message
  instead of attempting an opaque final no-tools call that can also hang.
- Transient model retry: retry `Concurrency limit exceeded`, `rate limit`,
  `too many requests`, and `please retry later` errors with short delays, and
  stream `retry` events while waiting.
- Tool timings: measure each `agent.run_tool` call and include `elapsed_ms` in
  the matching `tool_end` event.
- Tool IDs: include stable per-turn IDs such as `tool-1`, `tool-2` so the
  frontend can update the correct UI row.

The backend should reuse the existing WeChat progress concepts where practical:
tool labels, argument summaries, transient error detection, friendly busy
messages, and tool budget wording.

## Frontend Design

The chat surface will have three layers during a response:

- Assistant answer bubble: keeps streaming public text token by token.
- Progress timeline: shows concise status rows by default.
- Expandable tool details: shows full tool name, parameters, result preview,
  elapsed time, and error detail when the user opens a row.

Default timeline examples:

```text
正在思考…
#1 正在读取 Canvas Quiz：course=ECE2300
#1 已完成（1.2s）
#2 正在汇总 Canvas 总览
模型服务忙，2s 后自动重试（第 1 次）
开始生成回复…
```

Frontend state changes:

- Replace the single `currentToolCard` with a `Map` keyed by `tool_start.id`.
- If a legacy event lacks `id`, fall back to a generated sequential ID.
- Update tool rows by ID when `tool_end` arrives.
- Keep default rows compact; allow users to expand a tool row for full JSON.
- Mark rows as `running`, `done`, `error`, or `limited`.
- On `tool_limit`, `retry`, and friendly backend errors, update the timeline so
  the user is never left at "正在思考" or "等待中".

Canvas tool labels should be expanded to cover:

- `list_canvas_courses`
- `get_canvas_course_announcements`
- `get_canvas_course_quizzes`
- `get_canvas_course_updates`
- `get_canvas_overview`
- `get_canvas_todo`
- `configure_canvas_monitor`
- `list_canvas_assignments`
- `submit_canvas_assignment`
- `setup_canvas`

The UI should not expose hidden model reasoning. It should expose observable
execution: statuses, tool names, arguments, result previews, timings, retries,
limits, and errors.

## Concurrency Design

The frontend already prevents duplicate sends with `isSending`, but the backend
also needs a guard because refreshes, multiple tabs, or manual requests can
bypass frontend state.

Short-term design:

- Add a process-local chat lock around one `/api/chat` turn.
- If a second request arrives while a turn is active, return an SSE error/status
  saying the previous request is still running and the user should wait or retry
  after it finishes.
- Release the lock in a `finally` path when streaming completes or the client
  disconnects.

This does not solve multi-user hosting, but the web UI is a local personal
server, so a process-local lock fits the current deployment model.

## Error Handling

User-facing errors should be actionable and Chinese-first.

- Transient upstream concurrency/rate errors: stream retry status, retry a small
  fixed number of times, then return a friendly busy message.
- Tool budget reached: stream `tool_limit`, stop further tool execution, and ask
  the model or backend to summarize what is already known.
- Repeated tool loop: stop after 3 identical tool signatures and tell the user
  the course/query may be too vague.
- Tool execution exception: emit a tool row error and continue to a final answer
  when possible.
- Client disconnect: stop writing, release the backend chat lock, and avoid
  leaving the server stuck in a busy state.

## Testing Plan

Backend unit tests:

- Streams `tool_start` and `tool_end` with stable IDs, indexes, labels,
  summaries, and elapsed time.
- Stops after total tool budget and emits `tool_limit`.
- Stops repeated identical tool calls after 3 executions.
- Retries transient model concurrency errors and emits `retry`.
- Returns a friendly busy message if transient errors persist.
- Releases the chat lock after normal completion and after errors.

Frontend verification:

- Multiple tool starts before tool ends update the correct rows by ID.
- Canvas tool labels render as Chinese progress labels.
- `retry`, `tool_limit`, and `error` events replace the indefinite thinking
  state with explicit status.
- The details panel can expand/collapse and shows parameters plus result preview.

Manual verification:

- Start the web UI and ask a Canvas quiz or Canvas overview question.
- Confirm text streams as it is generated.
- Confirm progress rows appear immediately for thinking, tools, retries, and
  answer generation.
- Confirm no request remains stuck at "正在思考" or "等待中" after a limit or
  error condition.

## Acceptance Criteria

- A web Canvas query that triggers tools shows visible progress within one SSE
  event after the model chooses a tool.
- Multiple tool calls in one model response render as separate rows and finish
  independently.
- A repeated or runaway tool loop stops with a clear message before exceeding 12
  total tool calls.
- Upstream concurrency-limit errors no longer show raw provider text as the main
  user-facing answer.
- The web agent still streams final assistant text token by token.
- Tests cover backend loop control and event emission behavior.
