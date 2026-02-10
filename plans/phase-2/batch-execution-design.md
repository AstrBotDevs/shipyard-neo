# Batch 执行方案设计

## 背景

在 `plans/phase-2/mcp-browser-skill-design.md` 中提到：

> 为减少网络往返开销，Gull 新增批量执行端点。
> 这是纯性能优化，不改变编排模型。
> 不需要脚本解析器 — 就是 for 循环调用已有的 `_run_agent_browser()`。

我们需要设计这个 `POST /exec_batch` 端点，以及相应的 SDK 和 MCP 封装。

## 设计目标

1.  **原子性与错误处理**：批量执行是一组有序命令，应该支持"遇到错误即停止"或"继续执行"的策略。
2.  **结果聚合**：返回每一步的执行结果（stdout, stderr, exit_code）。
3.  **兼容性**：尽量复用现有的 `_run_agent_browser` 逻辑。
4.  **语法对齐**：Batch 中的命令字符串应与 `agent-browser` CLI 保持一致（不带 prefix）。

## 1. Gull Runtime 设计

### API 定义

```python
class BatchCommand(BaseModel):
    cmd: str = Field(..., description="Command string (without agent-browser prefix)")
    # 未来可扩展: timeout, 等等

class BatchExecRequest(BaseModel):
    commands: list[str] = Field(..., min_items=1)
    timeout: int = Field(default=60, ge=1, le=600)  # 整体超时
    stop_on_error: bool = Field(default=True, description="Stop if a command fails")

class BatchStepResult(BaseModel):
    cmd: str
    stdout: str
    stderr: str
    exit_code: int
    step_index: int
    duration_ms: int

class BatchExecResponse(BaseModel):
    results: list[BatchStepResult]
    total_steps: int
    completed_steps: int
    success: bool # All steps succeeded (or ignored errors)
    duration_ms: int
```

### 逻辑实现 (`pkgs/gull/app/main.py`)

新增 `/exec_batch` 端点：

1.  接收 `commands` 列表。
2.  循环调用 `_run_agent_browser`。
3.  计算剩余超时时间（总超时 - 已消耗时间）。
4.  如果 `stop_on_error=True` 且 `exit_code != 0`，则中断循环。
5.  聚合结果返回。

## 2. Bay API & Adapter 设计

### Adapter (`pkgs/bay/app/adapters/gull.py`)

新增 `exec_browser_batch` 方法：

```python
async def exec_browser_batch(
    self,
    commands: list[str],
    *,
    timeout: int = 60,
    stop_on_error: bool = True
) -> BatchExecutionResult:
    # POST /exec_batch to Gull
```

### Router (`pkgs/bay/app/router/capability/capability.py`)

新增 `exec_browser_batch` 方法，路由到 Adapter。

### API Endpoint (`pkgs/bay/app/api/v1/capabilities.py`)

新增 `POST /{sandbox_id}/browser/exec_batch`：

- Request: `BrowserExecBatchRequest`
- Response: `BrowserExecBatchResponse`
- 记录 Execution History：
    - 建议记录为一个整体 execution，type=`browser_batch`？或者记录多个？
    - **决策**：为了保持 History 简洁，记录为**单条** Execution，`exec_type=browser_batch`。`code` 字段存储所有命令的组合（换行分隔），`output` 聚合所有 stdout。详情放在 `data` 字段中。

## 3. SDK 设计 (`shipyard-neo-sdk`)

### `BrowserCapability` (`shipyard-neo-sdk/shipyard_neo/capabilities/browser.py`)

新增 `exec_batch` 方法：

```python
async def exec_batch(
    self,
    commands: list[str],
    *,
    timeout: int = 60,
    stop_on_error: bool = True
) -> BrowserBatchExecResult:
    # Call API
```

### Types (`shipyard-neo-sdk/shipyard_neo/types.py`)

新增 `BrowserBatchExecResult` 及相关模型。

## 4. MCP 设计 (`shipyard-neo-mcp`)

新增工具 `execute_browser_batch`：

```python
Tool(
    name="execute_browser_batch",
    description="Execute a sequence of browser automation commands in order.",
    inputSchema={
        "type": "object",
        "properties": {
            "sandbox_id": {"type": "string"},
            "commands": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of commands (without agent-browser prefix)"
            },
            "timeout": {"type": "integer", "default": 60},
            "stop_on_error": {"type": "boolean", "default": True}
        },
        "required": ["sandbox_id", "commands"]
    }
)
```

## 语法示例

参考 `references/agent-browser/skills/agent-browser/references/commands.md`，Batch 命令示例：

```json
{
  "commands": [
    "open https://example.com/login",
    "wait --load networkidle",
    "snapshot -i",
    "fill @e1 'user@example.com'",
    "fill @e2 'password'",
    "click @e3",
    "wait --load networkidle",
    "snapshot -i"
  ]
}
```

注意：这里假设 Agent 已经知道 `@e1`, `@e2` 等 refs（例如前一个单步 `snapshot` 拿到的）。如果是盲操作，Batch 的价值更在于确定的序列（如 `open` -> `screenshot`）。

如果是在 Batch 内部使用 snapshot 产生的 ref，由于无法在中间解析，Batch **不适合** 需要中间推理的流程（即："snapshot -> AI 看图 -> 决定点哪里" 这种闭环不能在一次 Batch 中完成）。

**Batch 的适用场景**：
1.  **初始化序列**：`open url` + `wait` + `snapshot`
2.  **确定性操作**：`fill` + `click` + `wait` (已知 refs)
3.  **信息采集**：`screenshot` + `get text` + `get url`

## 实施步骤补充

在 `plans/mcp-sdk-gap-analysis.md` 的基础上，细化 Batch 的实施：

1.  **Gull**: 实现 `POST /exec_batch`
2.  **Bay**: Adapter & API 支持 Batch
3.  **SDK**: 添加 `exec_batch`
4.  **MCP**: 添加 `execute_browser_batch` 工具

这个设计与 `plans/phase-2/mcp-browser-skill-design.md` 完全一致，是对其技术细节的补充。
