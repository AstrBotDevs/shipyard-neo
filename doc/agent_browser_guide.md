# agent-browser（经 Gull 透传）操作指南

> 在 Shipyard Neo 中，浏览器自动化由 Gull 运行时负责执行。
>
> **最重要的约定（本项目默认）**：
>
> - 你传给 Gull 的 `cmd` **不需要**也**不应该**带 `agent-browser` 前缀。
> - `--session` 与 `--profile` 由 Gull 自动注入并管理（分别来自 `SANDBOX_ID` / `BAY_SANDBOX_ID`，以及固定目录 `/workspace/.browser/profile/`）。
> - 因为 Gull 会在命令前注入参数，所以**不要在 `cmd` 里再写** `--session` / `--profile`。
>
> 相关文档：
> - [Gull 浏览器运行时操作文档](gull_browser_runtime.md)
> - Bay API：[`/v1/sandboxes/{sandbox_id}/browser/exec`](bay_api_v1.md#23-浏览器命令执行)

## 目录

- [1. 快速上手（Gull /exec）](#1-快速上手gull-exec)
- [2. 写法规则（很重要）](#2-写法规则很重要)
- [3. 核心工作流（Navigate → Snapshot → Interact → Re-snapshot）](#3-核心工作流navigate--snapshot--interact--re-snapshot)
- [4. 命令参考（以 Gull cmd 形式展示）](#4-命令参考以-gull-cmd-形式展示)
- [5. 快照与 Refs（@e1/@e2）](#refs)
- [6. 会话与状态持久化（Gull 自动管理）](#session)
- [7. 语义定位器（find）](#7-语义定位器find)
- [8. JavaScript（eval）](#8-javascripteval)
- [9. 文件产物（截图/PDF/下载/上传）](#9-文件产物截图pdf下载上传)
- [10. 高级功能（认证/录屏/代理）](#10-高级功能认证录屏代理)
- [11. 模板（改写为 /exec_batch 版本）](#11-模板改写为-exec_batch-版本)
- [12. 常见问题](#12-常见问题)
- [附录 A：进入容器直接调试 agent-browser CLI（可选）](#附录-a进入容器直接调试-agent-browser-cli可选)

---

## 1. 快速上手（Gull /exec）

Gull 对外提供最小 API：`POST /exec`，你只需要把“子命令字符串”放进 `cmd`。

```bash
curl -X POST http://localhost:8080/exec \
  -H 'Content-Type: application/json' \
  -d '{"cmd": "open https://example.com"}'
```

典型响应：

```json
{
  "stdout": "...",
  "stderr": "",
  "exit_code": 0
}
```

多步流程建议用 `POST /exec_batch`：

```bash
curl -X POST http://localhost:8080/exec_batch \
  -H 'Content-Type: application/json' \
  -d '{
    "commands": [
      "open https://example.com",
      "wait --load networkidle",
      "snapshot -i"
    ],
    "timeout": 60,
    "stop_on_error": true
  }'
```

---

## 2. 写法规则（很重要）

### 2.1 `cmd` 不是 shell

Gull 使用 [`asyncio.create_subprocess_exec()`](pkgs/gull/app/main.py:141) 直接执行命令，不经过 shell。

因此以下写法**不会生效**（它们是 shell 语法）：

- 重定向：`get text body > page.txt`
- 管道：`snapshot -i | grep ...`
- `&&`/`;` 连写命令

要“保存文本到文件”，正确做法是：

1) `POST /exec` 得到 `stdout`
2) 再用 Bay 的 filesystem API 写文件（`PUT /filesystem/files`）

### 2.2 带空格参数必须加引号

Gull 内部使用 `shlex.split()` 拆分 `cmd`（见 [`_run_agent_browser()`](pkgs/gull/app/main.py:136)），所以带空格参数必须用引号包起来：

- ✅ `fill @e1 "hello world"`
- ❌ `fill @e1 hello world`

### 2.3 全局参数的放置（可选）

如果你确实要用 agent-browser 的全局参数（例如 `--json`、`--proxy`），建议把它们放在 `cmd` 的**最前面**，位于子命令之前：

- ✅ `--json snapshot -i`
- ✅ `--proxy http://host:port open https://example.com`
- ⚠️ `open https://example.com --proxy ...`（不保证解析）

> 说明：Gull 会在命令最前面注入 `--session/--profile`，你的 `cmd` 会拼在它们后面。

---

## 3. 核心工作流（Navigate → Snapshot → Interact → Re-snapshot）

所有稳定的自动化都建议按以下节奏：

1. **导航**：`open <url>`
2. **快照**：`snapshot -i`（拿到 `@e1/@e2/...`）
3. **交互**：`click` / `fill` / `select` / `press` …
4. **页面变化后重新快照**：再次 `snapshot -i`

示例（建议用批量执行）：

```json
{
  "commands": [
    "open https://example.com/form",
    "wait --load networkidle",
    "snapshot -i",
    "fill @e1 \"user@example.com\"",
    "fill @e2 \"password123\"",
    "click @e3",
    "wait --load networkidle",
    "snapshot -i"
  ],
  "timeout": 90,
  "stop_on_error": true
}
```

### 1.1 在 Bay 中启用学习与回放

如果通过 Bay 的浏览器 capability 使用（`/v1/sandboxes/{sandbox_id}/browser/*`），可直接开启自迭代证据：

- `learn=true`：该次执行进入 browser learning 管线
- `include_trace=true`：返回 `trace_ref` 并持久化 step 轨迹

示例（单条）：

```json
{
  "cmd": "open https://example.com",
  "learn": true,
  "include_trace": true
}
```

示例（批量）：

```json
{
  "commands": ["open https://example.com", "click @e1", "fill @e2 \"hello\""],
  "learn": true,
  "include_trace": true
}
```

回放接口：

- `POST /v1/sandboxes/{sandbox_id}/browser/skills/{skill_key}/run`
- 返回 `execution_id`、`trace_ref`、step 结果

### 1.2 自迭代接口验收清单

建议每次改动 browser skill 相关代码后，按以下最小链路验收：

1. 单条执行（`learn=true, include_trace=true`）：
   - 响应有 `execution_id`、`trace_ref`
2. 批量执行（`learn=true, include_trace=false`）：
   - 响应有 `execution_id`，history 中仍有 `payload_ref`
3. 轨迹回查：
   - `GET /v1/sandboxes/{sandbox_id}/browser/traces/{trace_ref}` 可返回完整 step 数据
4. 回放接口：
   - 有 active release 时返回 step 结果
   - 无 active release 时返回 404（错误信息明确）

可直接运行：

```bash
cd pkgs/bay
uv run pytest -q tests/integration/core/test_browser_skill_e2e.py
```

---

## 4. 命令参考（以 Gull cmd 形式展示）

下面所有命令都可以直接作为 Gull `cmd` 传入（不带 `agent-browser` 前缀）。

### 4.1 导航

```text
open <url>      # 导航到 URL（aliases: goto, navigate）
back            # 后退
forward         # 前进
reload          # 刷新
close           # 关闭浏览器
connect 9222    # 通过 CDP 端口连接浏览器（需要运行方式支持）
```

### 4.2 快照（页面分析）

```text
snapshot              # 完整可访问性树
snapshot -i           # 仅交互元素（推荐）
snapshot -c           # 紧凑输出
snapshot -d 3         # 限制深度
snapshot -s "#main"   # 限定 CSS 区域
snapshot -i -C        # 包含 cursor-interactive 元素
snapshot @e9          # 仅查看某个容器 ref 的子树（如果 ref 可用）
```

### 4.3 交互（使用 snapshot 返回的 @ref）

```text
click @e1
dblclick @e1
focus @e1
fill @e2 "text"        # 清空后输入
type @e2 "text"        # 不清空
press Enter
press Control+a
keydown Shift
keyup Shift
hover @e1
check @e1
uncheck @e1
select @e1 "value"
select @e1 "a" "b"      # 多选
scroll down 500
scrollintoview @e1       # aliases: scrollinto
drag @e1 @e2
upload @e1 /workspace/file.pdf
```

### 4.4 获取信息

```text
get text @e1
get html @e1
get value @e1
get attr @e1 href
get title
get url
get count ".item"
get box @e1
get styles @e1
```

### 4.5 状态检查

```text
is visible @e1
is enabled @e1
is checked @e1
```

### 4.6 截图 / PDF

```text
screenshot                       # 保存到临时目录（路径由工具决定）
screenshot /workspace/a.png
screenshot --full /workspace/full.png
pdf /workspace/page.pdf
```

### 4.7 等待

```text
wait @e1
wait 2000
wait --text "Success"       # or -t
wait --url "**/dashboard"   # or -u
wait --load networkidle     # or -l
wait --fn "window.ready"    # or -f
```

### 4.8 标签页 / 窗口 / Frame / Dialog

```text
tab
tab new [url]
tab 2
tab close
window new
frame "#iframe"
frame main
dialog accept [text]
dialog dismiss
```

### 4.9 存储与网络

```text
cookies
cookies set name value
cookies clear
storage local
storage local key
storage local set k v
storage local clear

network route <url>
network route <url> --abort
network route <url> --body '{}'
network unroute [url]
network requests
network requests --filter api
```

---

<a id="refs"></a>

## 5. 快照与 Refs（@e1/@e2）

Refs 是 agent-browser 为“快照输出”分配的紧凑元素引用，能把复杂 DOM 交互降到更低 token 成本。

### 5.1 Snapshot 输出示例（概念）

```text
Page: Example Site - Home
URL: https://example.com

@e1 [header]
  @e2 [nav]
    @e3 [a] "Home"
    @e4 [a] "Products"
  @e6 [button] "Sign In"

@e7 [main]
  @e8 [h1] "Welcome"
  @e9 [form]
    @e10 [input type="email"] placeholder="Email"
    @e11 [input type="password"] placeholder="Password"
    @e12 [button type="submit"] "Log In"
```

### 5.2 Ref 生命周期（必须遵守）

**重要**：页面变化后 refs 会失效。

```text
snapshot -i
click @e1            # 页面跳转/DOM 重建
snapshot -i          # 必须重新获取 ref
click @e1            # 这里的 @e1 是新页面的元素
```

典型会触发 ref 失效的操作：

- 点击会导航的链接/按钮
- 表单提交
- 弹窗/下拉等动态内容展开（DOM 变化明显时）

### 5.3 Troubleshooting

- 报 `Ref not found`：直接 `snapshot -i` 重新拿 ref
- 元素不在快照里：先 `scroll` 或 `wait`，然后再 snapshot
- 元素很多：优先 `snapshot -s "#main"` 或 `snapshot @eX` 缩小范围

---

<a id="session"></a>

## 6. 会话与状态持久化（Gull 自动管理）

### 6.1 本项目的“会话”语义

在原生 agent-browser 中，`--session <name>` 用于隔离不同浏览器上下文。

在 Shipyard Neo 中：

- 每个 Sandbox 的 Gull 容器会绑定一个 session（来自 `SANDBOX_ID` / `BAY_SANDBOX_ID`）。
- 你**不需要**也**不应该**在 `cmd` 里手动设置 session。

> 如果你需要“多 session 并行”，推荐方式是**创建多个 Sandbox**（每个 Sandbox 自带独立 Gull session），而不是在一个 Gull 里手动切 session。

### 6.2 持久化（profile）语义

Gull 固定注入 `--profile /workspace/.browser/profile/`，持久化内容包括：

- Cookies
- localStorage / sessionStorage
- IndexedDB
- Cache

这意味着：

- 同一 Sandbox 的浏览器登录态可以跨容器重启复用
- Sandbox 删除时，Cargo Volume 被删，浏览器状态也随之回收

### 6.3 `state save` / `state load`（可选）

如果你确实需要显式导出/导入状态（例如迁移、调试），可以把状态文件写入 `/workspace`：

```text
state save /workspace/auth-state.json
state load /workspace/auth-state.json
```

> 安全提醒：state 文件包含敏感 token，严禁写入仓库或泄露。

---

## 7. 语义定位器（find）

当 ref 不稳定、或者你想用更“语义化”的方式定位元素时，可以使用 `find`：

```text
find role button click --name "Submit"
find text "Sign In" click
find text "Sign In" click --exact
find label "Email" fill "user@test.com"
find placeholder "Search" type "query"
find testid "submit-btn" click
find first ".item" click
find last ".item" click
find nth 2 "a" hover
```

---

## 8. JavaScript（eval）

基础用法：

```text
eval document.title
eval 2+2
```

复杂脚本注意：

- 通过 Gull `/exec` **无法**像 shell 那样 `cat <<EOF | ...` 给 `stdin` 传入脚本。
- 如果 agent-browser 支持 `eval -b <base64>`，它更适合在 HTTP 透传场景使用（把脚本编码成一个参数）。

---

## 9. 文件产物（截图/PDF/下载/上传）

### 9.1 保存截图 / PDF 到共享卷

```text
screenshot /workspace/screenshots/page.png
screenshot --full /workspace/screenshots/full.png
pdf /workspace/reports/page.pdf
```

然后通过 Ship 的 filesystem capability 下载：

- `GET /v1/sandboxes/{id}/filesystem/download?path=screenshots/page.png`

### 9.2 上传文件

`upload` 需要文件路径对 Gull 容器可见。推荐先用 filesystem 上传到 `/workspace/...`，再执行：

```text
upload @e1 /workspace/input/file.pdf
```

### 9.3 保存文本输出（正确方式）

因为 `cmd` 不是 shell，不能用 `>` 重定向。

正确方式：

1) `POST /exec {"cmd": "get text body"}` 拿到 `stdout`
2) 用 `PUT /filesystem/files` 把内容写入例如 `page-text.txt`

---

## 10. 高级功能（认证/录屏/代理）

### 10.1 认证（登录态复用）

典型流程：

```text
open https://app.example.com/login
wait --load networkidle
snapshot -i
fill @e1 "user@example.com"
fill @e2 "password123"
click @e3
wait --url "**/dashboard"
```

在 Gull 的默认 profile 持久化下：

- 只要不删 Sandbox/Cargo，后续再次 `open https://app.example.com/dashboard` 通常可以直接进入（依赖站点实现）。

### 10.2 视频录制

如果 agent-browser 支持录制命令，可把录制文件写到 `/workspace`：

```text
record start /workspace/recordings/demo.webm
open https://example.com
wait --load networkidle
snapshot -i
record stop
```

录制文件下载方式同截图（filesystem download）。

### 10.3 代理

两种配置方式（取决于你的部署形态）：

1) **容器级环境变量**（推荐，稳定）：在 Gull 容器启动时配置 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY`。

2) **命令级全局参数**（如果 agent-browser 支持且你需要动态切换）：把 `--proxy` 放在 `cmd` 最前面：

```text
--proxy http://proxy.example.com:8080 open https://httpbin.org/ip
get text body
```

> 注意：Shipyard Neo 的典型调用不会在每次请求层面修改容器环境变量；如需全局代理，建议作为 Profile/部署配置管理。

---

## 11. 模板（改写为 /exec_batch 版本）

### 11.1 表单自动化模板（对应 form-automation.sh 思路）

```json
{
  "commands": [
    "open https://example.com/form",
    "wait --load networkidle",
    "snapshot -i",
    "# 下面几条请根据 snapshot 输出替换 ref",
    "# fill @e1 \"Test User\"",
    "# fill @e2 \"test@example.com\"",
    "# click @e3",
    "# wait --load networkidle",
    "snapshot -i",
    "screenshot /workspace/form-result.png",
    "close"
  ],
  "timeout": 120,
  "stop_on_error": true
}
```

### 11.2 登录态复用模板（对应 authenticated-session.sh 思路）

```json
{
  "commands": [
    "open https://app.example.com/login",
    "wait --load networkidle",
    "snapshot -i",
    "# fill @e1 \"$APP_USERNAME\"",
    "# fill @e2 \"$APP_PASSWORD\"",
    "# click @e3",
    "# wait --url \"**/dashboard\"",
    "snapshot -i",
    "screenshot /workspace/login-ok.png"
  ],
  "timeout": 180,
  "stop_on_error": true
}
```

> 说明：在 HTTP 调用中，你的客户端侧负责提供用户名/密码；不要把真实凭据写进仓库。

### 11.3 内容采集模板（对应 capture-workflow.sh 思路）

```json
{
  "commands": [
    "open https://example.com",
    "wait --load networkidle",
    "screenshot --full /workspace/page-full.png",
    "snapshot -i",
    "pdf /workspace/page.pdf",
    "close"
  ],
  "timeout": 180,
  "stop_on_error": true
}
```

> 文本采集（`get text body`）会返回在 `stdout`，如需保存为文件请参见 [9.3](#93-保存文本输出正确方式)。

---

## 12. 常见问题

### 12.1 为什么文档里不写 `agent-browser` 前缀？

因为你是通过 Gull 的 HTTP 透传执行，Gull 会自动拼上 `agent-browser` 并注入 `--session/--profile`。

### 12.2 我能在 cmd 里传 `--session` / `--profile` 吗？

不建议：

- Gull 已经管理并注入这两个参数
- 手动传可能导致解析混乱
- 也会破坏 Sandbox 隔离与状态持久化约定

### 12.3 为什么 `get text body > a.txt` 不工作？

因为 `cmd` 不是 shell，Gull 不会解析 `>`。

### 12.4 为什么 `eval --stdin` 不适用于 Gull？

因为 Gull 的 `/exec` 没有为子进程提供 stdin 流；需要用单参数方式（例如 base64）或改为“容器内直接跑 CLI”。

---

## 附录 A：进入容器直接调试 agent-browser CLI（可选）

如果你进入 Gull 容器做交互调试，那么可以使用原始 CLI 形式：

```bash
agent-browser --session debug --profile /workspace/.browser/profile open https://example.com
agent-browser --session debug snapshot -i
agent-browser --session debug click @e1
```

但这不是 Shipyard Neo 的主路径，主路径请以“Gull cmd（不带前缀）”为准。
