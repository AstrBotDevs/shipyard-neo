# MCP 浏览器工具 + 操作技能设计

> Phase 2 补充设计：将 `agent-browser` 能力通过 MCP 暴露给 AI Agent，
> 并设计配套的操作技能（Skill）指导 Agent 组合使用工具。

## 背景

### 现状

- **Gull 容器** ([`pkgs/gull/app/main.py`](../../pkgs/gull/app/main.py)) 已封装 `agent-browser` CLI，提供 `POST /exec` 端点
- **Bay API** ([`pkgs/bay/app/api/v1/capabilities.py`](../../pkgs/bay/app/api/v1/capabilities.py)) 已有 `POST /{sandbox_id}/browser/exec` 端点
- **SDK** ([`shipyard-neo-sdk/shipyard_neo/capabilities/browser.py`](../../shipyard-neo-sdk/shipyard_neo/capabilities/browser.py)) 已有 `BrowserCapability.exec()` 方法
- **MCP Server** ([`shipyard-neo-mcp/src/shipyard_neo_mcp/server.py`](../../shipyard-neo-mcp/src/shipyard_neo_mcp/server.py)) 暴露 18 个工具，**尚无浏览器相关工具**

### 参考项目

工作区内有一个独立的 `skills/agent-browser/` 技能包，为本地 CLI 直接调用 `agent-browser` 提供操作指南。
本设计分析如何将其适配到 Shipyard 沙箱 + MCP 架构中。

## 核心设计决策

### 决策 1：命令前缀策略 — 不带 prefix

Gull 的 `/exec` 接口设计为 **不带 `agent-browser` 前缀**：

```python
# pkgs/gull/app/main.py _run_agent_browser()
parts = ["agent-browser"]          # 硬编码注入
parts.extend(["--session", session])   # 自动注入 session 隔离
parts.extend(["--profile", profile])   # 自动注入持久化配置
parts.extend(shlex.split(cmd))        # 用户的 cmd 不含 prefix
```

**保持这个设计**，理由：

1. **安全边界**：Gull `/exec` 只能执行 `agent-browser` 子命令，不能注入任意 shell 命令
2. **上下文自动注入**：`--session`（沙箱隔离）和 `--profile`（Cargo Volume 持久化）由 Gull 自动管理
3. **API 简洁**：调用方只需关心 agent-browser 子命令语义

**潜在混淆点及应对**：

| 场景 | 调用方式 | 问题 | 应对 |
|------|---------|------|------|
| Agent 在 Ship 里 `execute_shell("agent-browser open ...")` | Ship 容器 | Ship 没装 agent-browser，失败 | MCP 工具描述中明确说明 |
| Agent 在 Gull 里 `execute_browser("agent-browser open ...")` | Gull 容器 | 会变成 `agent-browser agent-browser open ...`，失败 | 参数描述中强调 "不带 prefix" |

### 决策 2：脚本序列执行 — 结构化批量，不做脚本注入

#### 问题

`skills/agent-browser/` 中的工作流模板（如 `authenticated-session.sh`）混合了三种内容：

```bash
# 1. agent-browser 命令（带前缀）
agent-browser open "$LOGIN_URL"

# 2. 普通 bash 命令
echo "Session restored successfully"
rm -f "$STATE_FILE"

# 3. bash 控制流
CURRENT_URL=$(agent-browser get url)
if [[ "$CURRENT_URL" != *"login"* ]]; then
    ...
fi
```

这种脚本**不能直接在 Shipyard 沙箱中运行**：
- Ship 容器没有 `agent-browser`
- Gull 容器的 `/exec` 不支持 bash 脚本
- 即使给 Gull 加 `/exec_script`，也会引入通用 shell 执行能力，破坏安全边界

#### 设计决策：Agent 做编排，不做脚本注入

**核心考量：Agent 比 bash 更擅长做决策**

传统 CLI 工具使用 bash 脚本做工作流编排，是因为没有更好的决策者。
但在 AI Agent 场景中，Agent 本身就是智能编排层：

| 对比维度 | bash 脚本编排 | Agent 编排 |
|---------|-------------|-----------|
| 条件判断 | 只能做字符串匹配（`if [[ "$URL" == *"login"* ]]`） | 可以理解 DOM 语义（看到 snapshot 后判断"这是登录页"） |
| 错误恢复 | 只能 `exit 1` 或 `retry` | 可以分析错误原因，换一种策略重试 |
| 动态元素 | 不能处理 Ref 变化 | 看到新 snapshot 后自然适应 |
| 调试 | 脚本内部不透明 | 每步 MCP 调用都有完整上下文 |
| 复用性 | 硬编码在脚本中 | Agent 根据 Skill 指引灵活组合 |

**典型场景对比**：

```
# bash 脚本方式（agent-browser skill 的 authenticated-session.sh）
if [[ -f "$STATE_FILE" ]]; then
    agent-browser state load "$STATE_FILE"
    agent-browser open "$LOGIN_URL"
    CURRENT_URL=$(agent-browser get url)
    if [[ "$CURRENT_URL" != *"login"* ]]; then
        echo "Session restored"
        exit 0
    fi
fi
# ... 执行登录流程 ...

# Agent 编排方式（通过 MCP 工具组合）
# 步骤 1: Agent 尝试打开目标页面
execute_browser(cmd="open https://app.example.com/dashboard")
execute_browser(cmd="snapshot -i")
# 步骤 2: Agent 看到 snapshot 内容，判断是否在登录页
#   → 如果看到 "Welcome" 和用户名，说明已登录 → 结束
#   → 如果看到 "Sign In" 表单 → 执行登录流程
execute_browser(cmd="fill @e1 'user@example.com'")
execute_browser(cmd="fill @e2 'password'")
execute_browser(cmd="click @e3")
execute_browser(cmd="wait --load networkidle")
execute_browser(cmd="snapshot -i")
# 步骤 3: Agent 验证登录结果
#   → 成功 → 继续任务
#   → 失败 → 分析原因（验证码？密码错误？）
```

Agent 编排**天然具备 bash 脚本不具备的推理能力**，不需要我们实现脚本解析器。

#### 实施方案：Gull 新增 `POST /exec_batch`

为减少网络往返开销，Gull 新增批量执行端点。
这是**纯性能优化**，不改变编排模型。

```python
# pkgs/gull/app/main.py

class BatchExecRequest(BaseModel):
    commands: list[str]       # ["open https://...", "snapshot -i", "click @e1"]
    timeout: int = 60         # 整体超时（秒）
    stop_on_error: bool = True  # 某步失败是否停止

class BatchStepResult(BaseModel):
    cmd: str
    stdout: str
    stderr: str
    exit_code: int
    step_index: int

class BatchExecResponse(BaseModel):
    results: list[BatchStepResult]
    total_steps: int
    completed_steps: int
    success: bool

@app.post("/exec_batch", response_model=BatchExecResponse)
async def exec_batch(request: BatchExecRequest) -> BatchExecResponse:
    results = []
    for i, cmd in enumerate(request.commands):
        stdout, stderr, code = await _run_agent_browser(
            cmd,
            session=SESSION_NAME,
            profile=BROWSER_PROFILE_DIR,
            timeout=request.timeout,
        )
        results.append(BatchStepResult(
            cmd=cmd, stdout=stdout, stderr=stderr,
            exit_code=code, step_index=i,
        ))
        if request.stop_on_error and code != 0:
            break

    return BatchExecResponse(
        results=results,
        total_steps=len(request.commands),
        completed_steps=len(results),
        success=all(r.exit_code == 0 for r in results),
    )
```

**不需要脚本解析器** — 就是 for 循环调用已有的 `_run_agent_browser()`。

**何时用单条 vs 批量**：

| 使用场景 | 推荐方式 |
|---------|---------|
| 需要看中间结果做决策（如 snapshot 后判断点哪里） | 单条 `execute_browser` |
| 确定性序列，不需要中间判断（如 open → fill → click → wait） | 批量 `execute_browser_batch` |
| 复杂条件流程（如登录判断、错误恢复） | Agent 编排多次单条调用 |

### 决策 3：跨容器协调

Gull（浏览器）和 Ship（Python/Shell/文件系统）运行在不同容器中，共享 Cargo Volume（`/workspace`）。

工作流示例：
```
Agent → execute_browser(cmd="screenshot /workspace/page.png")  → Gull 容器写入文件
Agent → read_file(path="page.png")                             → Ship 容器读取文件
Agent → execute_python(code="process_image('page.png')")       → Ship 容器处理
```

**不需要额外设计** — Cargo Volume 共享已经解决了数据传递问题。
Agent 自然地在 MCP 层做跨容器编排。

## 实施计划

### 阶段 1：补齐 MCP 浏览器工具

| 步骤 | 内容 | 涉及文件 |
|------|------|---------|
| 1.1 | MCP 新增 `execute_browser` 工具 | `shipyard-neo-mcp/src/shipyard_neo_mcp/server.py` |
| 1.2 | 补充单测 | `shipyard-neo-mcp/tests/test_server.py` |

### 阶段 2：批量执行能力

| 步骤 | 内容 | 涉及文件 |
|------|------|---------|
| 2.1 | Gull 新增 `POST /exec_batch` 端点 | `pkgs/gull/app/main.py` |
| 2.2 | Gull 单测 | `pkgs/gull/tests/unit/test_runner.py` |
| 2.3 | `GullAdapter` 新增 `exec_browser_batch()` | `pkgs/bay/app/adapters/gull.py` |
| 2.4 | `BaseAdapter` 新增默认实现 | `pkgs/bay/app/adapters/base.py` |
| 2.5 | `CapabilityRouter` 新增路由 | `pkgs/bay/app/router/capability/capability.py` |
| 2.6 | Bay API 新增端点 | `pkgs/bay/app/api/v1/capabilities.py` |
| 2.7 | SDK `BrowserCapability` 新增 `exec_batch()` | `shipyard-neo-sdk/shipyard_neo/capabilities/browser.py` |
| 2.8 | MCP 新增 `execute_browser_batch` 工具 | `shipyard-neo-mcp/src/shipyard_neo_mcp/server.py` |

### 阶段 3：操作技能（Skill）

| 步骤 | 内容 | 涉及文件 |
|------|------|---------|
| 3.1 | 创建 `skills/shipyard-sandbox/SKILL.md` | 新文件 |
| 3.2 | 创建浏览器参考 `references/browser.md` | 新文件（从 `skills/agent-browser/` 适配） |
| 3.3 | 创建技能生命周期参考 `references/skills-lifecycle.md` | 新文件 |
| 3.4 | 创建工具速查 `references/tools-reference.md` | 新文件 |

## 不做什么

| 排除项 | 原因 |
|--------|------|
| Gull `/exec_script`（bash 脚本注入） | 破坏安全边界，Agent 编排更优 |
| 改 `/exec` 为带 prefix | 失去安全隔离和上下文注入 |
| 在 Ship 容器安装 agent-browser | 职责混乱，违反单一容器单一职责 |
| 直接复用 agent-browser skill 脚本模板 | 本地 CLI 模式与沙箱 MCP 模式不兼容 |
