# Canvas 课程查询与监控设计

日期：2026-06-12

## 目标

把 `sjtu-agent` 现有的 Canvas 能力，从“只辅助查看 DDL/作业截止”扩展成一个面向具体课程的 Canvas 助手。

第一阶段先增加稳定的 Agent 工具，用来查看某一门课的公告、测验、作业和近期动态。第二阶段再增加定时监控能力，自动发现 Canvas 上的新内容或时间变化，并通过现有通知渠道提醒用户。

实现时继续使用运行时 `config.json` 里已有的 `canvas_base_url` 和 `canvas_token`。不能打印 token，也不能把 token 复制保存到现有配置以外的地方。

## 当前项目现状

目前 Canvas 相关逻辑分散在几个地方：

- `ddl_checker.py`：抓取 Canvas 作业和提交状态，用于 DDL 列表。
- `sjtu_agent/news_aggregator/sources/canvas.py`：抓取 Canvas 公告，用于新闻日报，但不是面向单门课查询。
- `sjtu_agent/agent/tools/_core.py`：包含 Canvas token 配置、Canvas 作业列表、Canvas 作业提交等工具。
- `scripts/remind_check.py`：监控本地提醒事项和 DDL 紧急保底提醒。
- `sjtu_agent/scheduler/*`：管理后台服务，例如 `remind-check`、`email-watcher` 和各类 bot。

现有结构可以工作，但如果继续把课程级查询和监控逻辑直接塞进 `_core.py` 或 `ddl_checker.py`，Canvas 逻辑会更分散。更合适的做法是新增一个小而集中的 Canvas 能力层，后续查询工具、新闻源、DDL 逻辑和 watcher 都可以复用它。

## 已验证的 Canvas API 能力

使用用户本地已经配置好的 SJTU Canvas token，对 `https://oc.sjtu.edu.cn` 做了只读探测。结果如下：

- 当前 token 能看到 13 门 active 课程。
- 课程列表和课程详情可用：`/api/v1/courses`、`/api/v1/courses/:course_id`。
- 所有 active 课程都能读取 tabs：`/api/v1/courses/:course_id/tabs`。
- 课程公告可用：`/api/v1/announcements`，但必须带 `context_codes[]=course_<id>`。不带课程上下文会返回 `400 Missing context_codes`。
- 所有 active 课程都能读取作业：`/api/v1/courses/:course_id/assignments`。
- Classic Quizzes 可用接口是 `/api/v1/courses/:course_id/quizzes`。探测中 6 门课返回了 quiz 数据，7 门课返回“该页面已对此课程禁用”这类正常的课程功能禁用响应。
- New Quizzes 路径，例如 `/api/quiz/v1/courses/:course_id/quizzes`，在当前 SJTU Canvas 上返回 `404`，所以不能作为主实现依赖。
- modules、folders、files、discussion topics、teachers、course activity stream、user todo、planner items、user activity stream 都可以读取。
- calendar events 本次探测没有返回有用内容，不建议作为监控主数据源。

工具实现时应把“某门课禁用了某个 Canvas 功能”当成正常状态处理，而不是当成 token 失效或系统故障。

## 产品范围

### 第一阶段：查询工具

新增 Agent 工具，让用户可以按课程名或 course ID 查询 Canvas 内容：

- 列出 active Canvas 课程及重要元数据。
- 根据课程名、课程代码或 course ID 解析具体课程。
- 查看某门课的公告。
- 查看某门课的 quiz，优先使用 Classic Quizzes。
- 从 assignments 里补充识别 quiz-backed 作业，避免漏掉以作业形式出现的测验。
- 聚合某门课的公告、quiz、作业和近期动态，形成课程更新摘要。
- 在需要时查看全局 Canvas todo/planner items，用于回答“现在有什么要做”的问题。

### 第二阶段：监控服务

新增一个后台 watcher，复用第一阶段的 Canvas 能力层：

- 发现新公告。
- 发现新 quiz，以及 quiz 状态变化。
- 发现 quiz 的 due/unlock/lock 时间变化。
- 可选监控新作业和作业 due 时间变化。
- 用本地状态文件避免重复提醒。
- 首次运行只建立基线，不推送所有历史内容。
- 尽量复用现有通知渠道。

## 不做的事情

本设计不包含：

- 向 Canvas 写入内容。
- 提交 quiz。
- 抓取 quiz 题目或答案。
- 强依赖 New Quizzes API。
- 替代现有 Canvas 作业提交功能。
- 替代现有新闻日报。
- 大型 UI 改造。

## 架构设计

新增一个聚焦 Canvas 的模块，推荐命名为 `sjtu_agent/canvas_client.py`，或者放在小包 `sjtu_agent/canvas/` 下。

这个模块负责：

- 从现有运行时配置读取 Canvas base URL 和 token。
- 用 `requests.Session` 封装 Canvas API 请求。
- 提供 Canvas 分页读取 helper。
- 提供课程解析 helper。
- 把 Canvas 原始响应规范化成普通字典，保持和当前 tool 风格兼容。
- 对常见错误做友好分类，例如未配置 token、token 失效、课程功能禁用、资源不存在、请求超时、Canvas 返回结构异常。

初始公开方法建议：

- `list_courses()`
- `get_course(course_id)`
- `resolve_course(query)`
- `list_announcements(course_id, limit, since)`
- `list_quizzes(course_id, include_past=False, include_assignment_backed=True)`
- `list_assignments(course_id, include_past=False)`
- `list_activity(course_id, limit)`
- `list_todo()`
- `list_planner_items(limit)`
- `get_course_updates(course_id, include, limit)`

第一轮实现只需要让新增 tools 和 watcher 使用这个模块。已有 `ddl_checker.py`、news source、assignment submit 等逻辑可以后续逐步迁移，不在本轮强行重构，避免扩大风险。

## Agent Tools 设计

按照现有 tool registry 风格，新增 tool definitions 和 dispatch 分支。

推荐新增以下工具：

### `list_canvas_courses`

参数：

- `include_tabs`：boolean，默认 `false`
- `include_teachers`：boolean，默认 `false`

返回：

- `count`
- `courses`
- 每门课包含：`course_id`、`name`、`course_code`、`workflow_state`、`default_view`
- 如果请求了额外信息，再包含 `tabs`、`teachers`

### `get_canvas_course_announcements`

参数：

- `course`：字符串或整数 course ID
- `limit`：整数，默认 20
- `since_days`：可选整数

返回：

- 解析后的课程信息
- 公告数量
- 公告列表，字段包括 `id`、`title`、`posted_at`、`author`、`summary`、`html_url`、可用时包含 `read_state`

### `get_canvas_course_quizzes`

参数：

- `course`：字符串或整数 course ID
- `include_past`：boolean，默认 `false`；默认只返回仍有效的 quiz，需要查历史时显式打开
- `include_assignment_backed`：boolean，默认 `true`

返回：

- 解析后的课程信息
- quiz 功能状态：`enabled`、`disabled` 或 `unknown`
- quiz 列表，字段包括 `quiz_id`、`assignment_id`、`title`、`quiz_type`、`unlock_at`、`due_at`、`lock_at`、`time_limit`、`allowed_attempts`、`question_count`、`points_possible`、`published`、`locked_for_user`、`html_url`
- 如果 Classic Quizzes 被禁用或返回不完整，再补充 assignment-backed quiz

### `get_canvas_course_updates`

参数：

- `course`：字符串或整数 course ID
- `include`：字符串列表，默认 `["announcements", "quizzes", "assignments", "activity"]`
- `limit`：整数，默认 10
- `include_past`：boolean，默认 `false`；控制 quiz 和 assignment 是否包含已过期项目

返回：

- 解析后的课程信息
- 按内容类型分组的 sections
- 对禁用功能或不可用端点给出 warnings

### `get_canvas_todo`

参数：

- `limit`：整数，默认 20

返回：

- 规范化后的 Canvas todo items 和 planner items。

### `configure_canvas_monitor`

用户应该可以通过自然语言调整 Canvas watcher，而不是必须手动编辑 `config.json`。新增配置工具供 Agent 在用户明确要求“调整 Canvas 监控时间、课程范围、监控内容、通知渠道、暂停/开启监控”时调用。

参数：

- `enabled`：可选 boolean，用于开启或暂停 Canvas watcher。
- `interval_seconds`：可选整数，直接设置检查间隔秒数。
- `interval_minutes`：可选数字，用分钟设置检查间隔，Agent 处理“每 10 分钟查一次”这类 prompt 时优先使用。
- `course_ids`：可选整数列表，指定要监控的 Canvas course ID。
- `course_filters`：可选字符串列表，按课程名或课程代码关键词过滤。
- `include_announcements`：可选 boolean。
- `include_quizzes`：可选 boolean。
- `include_assignments`：可选 boolean。
- `include_activity`：可选 boolean。
- `notify_channels`：可选字符串列表，限定为已有通知渠道，例如 `system`、`telegram`、`feishu`、`wechat`。
- `baseline_on_first_run`：可选 boolean。

行为要求：

- 只更新 `config.json` 的 `canvas_monitor` 配置块，不改 Canvas token、Cookie 或其他平台配置。
- 未传入的字段保留原值；若原来没有配置，则从 watcher 默认配置开始合并。
- `interval_minutes` 会换算成 `interval_seconds`，并优先于 `interval_seconds`。
- 最小检查间隔限制为 30 秒，避免误设过小造成 Canvas 请求过密。
- `course_ids` 优先于 `course_filters` 的语义保持不变；工具可以同时保存两者，但返回说明里要提示优先级。
- 返回完整生效配置、更新字段列表、配置文件路径和“正在运行的 watcher 会在下一轮循环读取新配置；如需立刻生效请重启 watcher”的提示。

这些 tools 应返回 JSON 字典，而不是已经格式化好的自然语言。Agent 和 bot 层再负责把 JSON 结果转成人类可读回答。

## 课程解析规则

课程解析需要支持：

- 精确数字 course ID。
- 精确课程名。
- 课程名部分匹配。
- `course_code` 部分匹配。
- 英文课程代码大小写不敏感匹配。

如果只匹配到一门课，就直接使用。若匹配到多门课，返回结构化的 ambiguity 响应，列出候选 course ID 和课程名。若没有匹配，返回结构化 not-found 响应，并附带少量 active courses 示例，方便用户换一种说法。

## Quiz 策略

Classic Quizzes 是主数据源：

- 调用 `/api/v1/courses/:course_id/quizzes`。
- `200` 且返回 list，表示正常 quiz 数据。
- `404` 且 message 表示“页面已禁用”，视为该课程没有启用 Quizzes 页面。
- 其他非 2xx 响应按严重程度返回 warning 或 error。

assignment-backed 补充逻辑：

- 调用 `/api/v1/courses/:course_id/assignments`。
- 包含 `submission_types` 中有 `online_quiz` 的 assignment。
- 包含 `quiz_id`、`is_quiz_assignment`、`original_quiz_id` 等字段提示为 quiz 的 assignment。
- 合并 Classic Quiz 记录时，优先用 `quiz_id`、`assignment_id` 或 URL 去重。

默认有效期过滤：

- 查询 quiz、课程更新、watcher 监控时默认排除已经过期的 quiz 和 assignment。
- 有效期判断优先使用 `lock_at`，没有 `lock_at` 时使用 `due_at`。
- 没有 `lock_at` 和 `due_at` 的项目保留，避免误删仍可访问但没有截止时间的 Canvas 项。
- 用户明确要求“历史”“过去”“已过期”内容时，Agent 应传 `include_past=true`。

New Quizzes 处理：

- 第一阶段默认不调用 New Quizzes API。
- Canvas client 的边界预留好，后续如果要做可选探测，可以不改变 tool 返回契约。

## 监控流程

新增脚本，例如 `scripts/canvas_watcher.py`，并增加 CLI 子命令 `sjtu-agent canvas-watcher`。

运行时 `config.json` 增加配置块：

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

配置含义：

- `course_ids` 和 `course_filters` 都为空时，监控所有 active 课程。
- `course_ids` 优先级高于 `course_filters`。
- 不支持或未配置的通知渠道跳过，并写日志。
- `baseline_on_first_run` 用来避免首次运行时推送大量历史消息。

状态文件：

- 存在 `DATA_DIR / "canvas_monitor_state.json"`。
- 按课程和内容类型记录已见过的 ID 和签名。
- 记录 `last_checked_at`。
- 对每个 item 保存签名，用于识别“同一个对象发生了变化”。

建议 key 形式：

- `announcement:<course_id>:<announcement_id>`
- `quiz:<course_id>:<quiz_id>`
- `quiz_assignment:<course_id>:<assignment_id>`
- `assignment:<course_id>:<assignment_id>`

签名字段：

- Announcement：`title`、`posted_at`、`updated_at`、`message_hash`
- Quiz：`title`、`unlock_at`、`due_at`、`lock_at`、`published`、`locked_for_user`、`question_count`、`points_possible`
- Assignment：`name`、`due_at`、`lock_at`、`unlock_at`、`published`、`submission_types`

通知行为：

- 新公告：推送标题、课程、发布时间、摘要、URL。
- 新 quiz：推送标题、课程、开放/截止/锁定时间、限时、尝试次数、URL。
- quiz 时间变化：推送变化前后的字段。
- 新作业或作业 due 时间变化：第二阶段可选，默认关闭，避免和现有 DDL guard 重复提醒。

## Scheduler 集成

把 `canvas-watcher` 加入：

- `sjtu_agent/cli.py`
- `sjtu_agent/scheduler/__init__.py`
- macOS launchd service specs
- Windows Task Scheduler/psmux specs
- Linux systemd specs
- README service list

服务应支持：

- `--once`：只检查一次。
- `--test`：打印将会发送的通知，但不真正推送。
- 默认行为要和平台调度方式一致。macOS/Linux 可以用 interval service；Windows Task Scheduler 可以按固定间隔执行一次性命令。

## 通知集成

优先复用现有通知 helper，避免重复写 Telegram/飞书/系统通知逻辑。

如果现有 helper 因为藏在脚本里而难以复用，可以在实现阶段引入一个小的内部通知模块，例如 `sjtu_agent/notifications.py`，再把共享发送逻辑迁移进去。

第二阶段最低可接受通知能力：

- 系统通知。
- 如果已经配置，则支持 Telegram 和飞书。
- WeChat 只有在现有 WeChat push helper 可以安全调用、且不会启动第二个 bot 会话时才接入。

## 错误处理

需要明确处理这些情况：

- 未配置 Canvas token：返回 setup 引导，不执行监控检查。
- token 无效或过期：展示 HTTP 状态，并建议重新运行 `setup_canvas`。
- 某门课禁用了某项 Canvas 功能：返回 `status: disabled`，其他 section 继续处理。
- 课程查询有歧义：返回候选列表。
- Canvas 请求超时：该 section 返回 warning，其他 section 尽量继续。
- Canvas 返回结构异常：返回 warning，并保留安全的字段名信息，但不要让 Agent 崩溃。

watcher 不应在状态保存成功前把 item 标记为已见。若通知发送部分失败，应避免无限重复推送，可以记录有限重试标记，或者把失败渠道单独写日志。

## 测试计划

增加基于 mock Canvas 响应的单元测试：

- 课程解析：course ID、精确名称、部分名称、歧义查询。
- 公告 endpoint 必须带 context codes，并能规范化公告字段。
- Classic quiz 成功返回。
- Classic quiz 页面禁用响应。
- assignment-backed quiz 补充识别。
- course updates 聚合时，某个 endpoint 失败也能返回部分结果和 warning。
- monitor 首次基线不发送通知。
- monitor 能发现新公告。
- monitor 能发现 quiz due 时间变化。
- 缺少 token 时返回 setup 引导。

CI 中不要跑真实 Canvas 网络测试。真实 Canvas 探测可以保留为本地手动诊断命令。

## 文档更新

实现完成后更新 README：

- 示例 Agent 问法。
- 新增 CLI 命令。
- Canvas monitor 配置块。
- 解释部分课程可能禁用某些 Canvas tabs。
- 解释当前 SJTU Canvas 看起来主要开放 Classic Quizzes，而 New Quizzes API 暂不可依赖。

## 推进顺序

1. 建 Canvas client 和第一阶段 Agent 查询 tools。
2. 给 client 规范化逻辑和 tools 行为加测试。
3. 用用户本地 Canvas token 手动验证查询 tools。
4. 新增 watcher、状态文件和通知集成。
5. 增加 scheduler 注册和 README 文档。
6. 跑完整测试，并本地执行一次 `--once --test` watcher 检查。
