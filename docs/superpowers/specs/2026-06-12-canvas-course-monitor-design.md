# Canvas Course Query and Monitor Design

Date: 2026-06-12

## Goal

Expand the Canvas support in `sjtu-agent` from deadline-only helpers into a course-specific Canvas assistant. The first phase adds reliable Agent tools for inspecting one course's announcements, quizzes, assignments, and recent activity. The second phase adds a scheduled monitor that detects new or changed Canvas items and sends notifications through existing channels.

The implementation must use the user's existing `canvas_base_url` and `canvas_token` from the runtime `config.json`. It must not print or persist tokens outside existing configuration.

## Current Project Context

Canvas functionality is currently split across several places:

- `ddl_checker.py` fetches Canvas assignments and submission state for DDL lists.
- `sjtu_agent/news_aggregator/sources/canvas.py` fetches Canvas announcements for the news digest, but only as a recent-news source.
- `sjtu_agent/agent/tools/_core.py` contains Canvas setup, assignment listing, and assignment submission tools.
- `scripts/remind_check.py` monitors local reminders and urgent DDL guard events.
- `sjtu_agent/scheduler/*` manages installable background services such as `remind-check`, `email-watcher`, and bot daemons.

The existing structure works, but adding course-specific Canvas querying and monitoring directly into `_core.py` or `ddl_checker.py` would further scatter Canvas logic. A small Canvas capability layer should be introduced and reused by tools, news sources, DDL code, and the future watcher.

## Verified Canvas API Capabilities

Read-only probing against the user's configured SJTU Canvas instance (`https://oc.sjtu.edu.cn`) showed:

- Active courses: 13 courses visible to the token.
- Course list and course details are available through `/api/v1/courses` and `/api/v1/courses/:course_id`.
- Course tabs are available for all active courses through `/api/v1/courses/:course_id/tabs`.
- Course announcements are available through `/api/v1/announcements`, but the request must include `context_codes[]=course_<id>`. Calling this endpoint without context codes returns `400 Missing context_codes`.
- Course assignments are available for all active courses through `/api/v1/courses/:course_id/assignments`.
- Classic Canvas quizzes are available through `/api/v1/courses/:course_id/quizzes` for courses whose Quizzes page is enabled. In the probe, 6 courses returned quiz data and 7 returned a normal course-level disabled-page response.
- New Quizzes API paths such as `/api/quiz/v1/courses/:course_id/quizzes` returned `404` on this SJTU Canvas instance, so New Quizzes must not be the primary implementation path.
- Modules, folders, files, discussion topics, teachers, course activity stream, user todo, planner items, and user activity stream are readable.
- Calendar events returned no useful items in the probe and should not be treated as the primary source for monitoring.

The query tools should treat a disabled course feature as a normal per-course condition, not as a fatal Canvas error.

## Product Scope

Phase 1: Query Tools

Add Agent tools that let the user ask for Canvas details by course name or course ID:

- List active Canvas courses and their important metadata.
- Resolve a course from partial name, course code, or course ID.
- Show course announcements.
- Show course quizzes using Classic Quizzes first.
- Supplement quizzes from assignments when assignments are quiz-backed or use `online_quiz`.
- Show a compact course update summary combining announcements, quizzes, assignments, and recent activity.
- Show global Canvas todo/planner items if useful for "what do I need to do now" questions.

Phase 2: Monitor

Add a background watcher that reuses the same Canvas capability layer:

- Detect new announcements.
- Detect new quizzes and quiz state changes.
- Detect quiz due/unlock/lock time changes.
- Optionally detect new assignments and assignment due changes.
- Maintain local state to avoid duplicate notifications.
- On first run, establish a baseline instead of pushing all historical items.
- Send notifications through existing notification channels where possible.

## Non-Goals

This design does not include:

- Writing to Canvas.
- Submitting quizzes.
- Scraping quiz questions or answers.
- Depending on New Quizzes as a required API.
- Replacing the existing assignment submission feature.
- Replacing the existing news digest.
- Large UI changes.

## Architecture

Introduce a focused Canvas module, preferably `sjtu_agent/canvas_client.py` or a small package under `sjtu_agent/canvas/`.

The module should provide:

- Configuration loading from existing runtime config.
- A `CanvasClient` wrapper around `requests.Session`.
- Pagination helpers for Canvas list endpoints.
- Course resolution helpers.
- Normalized data models represented as plain dictionaries for compatibility with current tool style.
- Friendly error classification for missing token, invalid token, disabled course feature, not found, timeout, and unexpected Canvas responses.

Initial public methods:

- `list_courses()`
- `get_course(course_id)`
- `resolve_course(query)`
- `list_announcements(course_id, limit, since)`
- `list_quizzes(course_id, include_assignment_backed=True)`
- `list_assignments(course_id, include_past=True)`
- `list_activity(course_id, limit)`
- `list_todo()`
- `list_planner_items(limit)`
- `get_course_updates(course_id, include, limit)`

Existing code can be migrated opportunistically in later work, but the first implementation only needs to use the new module from new tools and the watcher. This keeps the feature scoped and avoids risky refactors.

## Agent Tools

Add tool definitions and dispatch entries consistent with the existing tool registry style.

Recommended tools:

### `list_canvas_courses`

Parameters:

- `include_tabs`: boolean, default `false`
- `include_teachers`: boolean, default `false`

Returns:

- `count`
- `courses`
- For each course: `course_id`, `name`, `course_code`, `workflow_state`, `default_view`, optional `tabs`, optional `teachers`

### `get_canvas_course_announcements`

Parameters:

- `course`: string or integer course ID
- `limit`: integer, default 20
- `since_days`: optional integer

Returns:

- Resolved course information
- Announcement count
- Announcements with `id`, `title`, `posted_at`, `author`, `summary`, `html_url`, `read_state` when available

### `get_canvas_course_quizzes`

Parameters:

- `course`: string or integer course ID
- `include_past`: boolean, default `true`
- `include_assignment_backed`: boolean, default `true`

Returns:

- Resolved course information
- Quiz feature status: `enabled`, `disabled`, or `unknown`
- Quizzes with `quiz_id`, `assignment_id`, `title`, `quiz_type`, `unlock_at`, `due_at`, `lock_at`, `time_limit`, `allowed_attempts`, `question_count`, `points_possible`, `published`, `locked_for_user`, `html_url`
- Assignment-backed quiz supplements when the Classic Quizzes tab is disabled or incomplete

### `get_canvas_course_updates`

Parameters:

- `course`: string or integer course ID
- `include`: list of strings, default `["announcements", "quizzes", "assignments", "activity"]`
- `limit`: integer, default 10

Returns:

- Resolved course information
- Sections for the requested item types
- Warnings for disabled tabs or unavailable endpoints

### `get_canvas_todo`

Parameters:

- `limit`: integer, default 20

Returns:

- Canvas todo items and planner items in a normalized list.

The tools should return JSON dictionaries, not formatted prose. Bot and Agent layers can format the result for the user.

## Course Resolution

Course resolution must support:

- Exact numeric course ID.
- Exact course name.
- Partial course name match.
- Partial `course_code` match.
- Case-insensitive matching for English course codes.

If exactly one course matches, use it. If multiple courses match, return a structured ambiguity response with candidate course IDs and names. If no course matches, return a structured not-found response and include a short sample of active courses.

## Quiz Strategy

Classic Quizzes is the primary source:

- Call `/api/v1/courses/:course_id/quizzes`.
- A `200` response with a list is normal quiz data.
- A `404` response whose message says the page is disabled is a normal disabled-feature state.
- Other non-2xx responses become warnings or errors based on severity.

Assignment-backed supplement:

- Fetch `/api/v1/courses/:course_id/assignments`.
- Include assignments where `submission_types` contains `online_quiz`, or where fields such as `quiz_id`, `is_quiz_assignment`, or `original_quiz_id` indicate quiz backing.
- Merge with Classic Quiz records by `quiz_id`, `assignment_id`, or URL where possible.

New Quizzes:

- Do not call New Quizzes by default in Phase 1.
- Add the client boundary so a later optional probe can be introduced without changing tool contracts.

## Monitoring Workflow

Add a script such as `scripts/canvas_watcher.py` and a CLI subcommand `sjtu-agent canvas-watcher`.

Configuration block in runtime `config.json`:

```json
{
  "canvas_monitor": {
    "enabled": true,
    "course_ids": [],
    "course_filters": [],
    "include_announcements": true,
    "include_quizzes": true,
    "include_assignments": false,
    "include_activity": false,
    "interval_seconds": 300,
    "notify_channels": ["system", "telegram", "feishu", "wechat"],
    "baseline_on_first_run": true
  }
}
```

Interpretation:

- Empty `course_ids` and `course_filters` means monitor all active courses.
- `course_ids` takes priority over filters.
- Unsupported or unconfigured notification channels are skipped with a log message.
- `baseline_on_first_run` prevents historical flood.

State file:

- Store under `DATA_DIR / "canvas_monitor_state.json"`.
- Track seen IDs and signatures by item type and course.
- Include `last_checked_at`.
- Include per-item signatures for change detection.

Suggested key shapes:

- `announcement:<course_id>:<announcement_id>`
- `quiz:<course_id>:<quiz_id>`
- `quiz_assignment:<course_id>:<assignment_id>`
- `assignment:<course_id>:<assignment_id>`

Signature fields:

- Announcement: `title`, `posted_at`, `updated_at`, `message_hash`
- Quiz: `title`, `unlock_at`, `due_at`, `lock_at`, `published`, `locked_for_user`, `question_count`, `points_possible`
- Assignment: `name`, `due_at`, `lock_at`, `unlock_at`, `published`, `submission_types`

Notification behavior:

- New announcement: push title, course, posted time, summary, URL.
- New quiz: push title, course, open/due/lock times, time limit, attempts, URL.
- Quiz time change: push before/after fields.
- New assignment or assignment due change: optional in Phase 2, off by default to avoid duplicating existing DDL guard behavior.

## Scheduler Integration

Add `canvas-watcher` to:

- `sjtu_agent/cli.py`
- `sjtu_agent/scheduler/__init__.py`
- macOS launchd service specs
- Windows Task Scheduler/psmux specs
- Linux systemd specs
- README service list

The service should support:

- `--once` for a single check.
- `--test` for printing would-send notifications.
- Default continuous or scheduled behavior consistent with the selected platform. On macOS/Linux, interval service is acceptable. On Windows Task Scheduler, one-shot invocation at a configured interval is acceptable.

## Notification Integration

Prefer reusing existing notification helpers instead of duplicating channel-specific code.

If direct reuse is currently difficult because notification helpers live inside scripts, introduce a small internal notification module in a later implementation step, for example `sjtu_agent/notifications.py`, and migrate shared send logic there.

Minimum acceptable Phase 2 behavior:

- System notification.
- Telegram and Feishu if already configured.
- WeChat only if the existing WeChat push helper can be called without starting a second bot session.

## Error Handling

Handle these cases explicitly:

- Missing Canvas token: return setup guidance and do not run monitor checks.
- Invalid/expired token: surface HTTP status and suggest rerunning `setup_canvas`.
- Disabled course feature: return `status: disabled` and continue other sections.
- Ambiguous course query: return candidates.
- Canvas timeout: return a warning for that section and continue other sections where possible.
- Unexpected schema: include a warning and preserve raw-safe field names, but do not crash the agent.

The watcher must not mark items as seen until state is successfully saved after notification attempts. If notification sending partially fails, the implementation should avoid infinite duplicate storms by recording a bounded retry marker or logging the failed channel separately.

## Tests

Add unit tests with mocked Canvas responses:

- Course resolution by ID, exact name, partial name, and ambiguous query.
- Announcements endpoint requires context codes and normalizes announcement fields.
- Classic quiz success.
- Classic quiz disabled-page response.
- Assignment-backed quiz supplement.
- Course updates aggregation with partial endpoint failure.
- Monitor first-run baseline does not notify.
- Monitor detects new announcements.
- Monitor detects quiz due-time changes.
- Missing token produces setup guidance.

Avoid live Canvas network tests in CI. Live probing can remain a manual local diagnostic command if useful.

## Documentation

Update README after implementation with:

- Example Agent questions.
- New CLI commands.
- Canvas monitor config block.
- Explanation that some Canvas course tabs may be disabled by course settings.
- Explanation that SJTU currently appears to expose Classic Quizzes but not New Quizzes API.

## Rollout Plan

1. Build Canvas client and Phase 1 Agent tools.
2. Add tests for client normalization and tool behavior.
3. Manually verify tools against the user's configured Canvas token.
4. Add watcher, state file, and notification integration.
5. Add scheduler registration and README docs.
6. Run full test suite and one local `--once --test` watcher check.

