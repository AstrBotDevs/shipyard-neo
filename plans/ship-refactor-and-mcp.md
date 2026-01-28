# Ship 重构与 MCP 集成设计方案

## 1. 现状调研 (Survey)

我对 `pkgs/ship` 的当前实现进行了调研。代码库已经根据“单容器单会话”的设计理念进行了大幅简化。

### 1.1 当前状态
- **单会话架构**: 代码已经反映了单会话模型。
    - `user_manager.py` 使用固定的 `EXEC_USER = "shipyard"` 和 `WORKSPACE_ROOT = Path("/workspace")`。
    - `ipython.py` 使用单例 `_kernel_manager`。
    - `term.py` 按需创建 PTY，不再按 session ID 分组。
    - `filesystem.py` 和 `upload.py` 直接在固定的 workspace 根目录下操作。
- **遗留痕迹**:
    - `user_manager.py` 文件名具有误导性；它主要管理后台进程和 Shell 执行，而非“用户”。
    - 部分 API 端点可能仍暗示了旧模式（尽管 `session_id` 参数已基本移除或不再重要）。

### 1.2 差距分析 (Gap Analysis)
- **MCP 支持**: 目前 `ship` 仅暴露 REST API (`FastAPI`)，缺乏 MCP (Model Context Protocol) 支持。
- **Agent 协议**: 容器缺乏标准化的方式向 Manager/Orchestrator 报告其能力。
- **浏览器自动化**: 尚无内置的浏览器自动化能力。

## 2. 重构计划 (Refactoring Plan)

### 2.1 重命名 `user_manager` 为 `process_manager`
`user_manager.py` 组件名称已不准确。应将其重命名为 `process_manager.py`，以准确反映其角色：管理单一 `shipyard` 用户的后台进程和交互式 Shell 会话。

- **动作**: 将 `pkgs/ship/app/components/user_manager.py` 重命名为 `pkgs/ship/app/components/process_manager.py`。
- **动作**: 更新 `shell.py`, `term.py` 等文件中的所有导入。

### 2.2 清理工作
- 移除不再适用的“session”相关术语（注释、变量名）。
- 验证 `ipython.py` 单例逻辑的健壮性。

## 3. MCP 集成策略 (MCP Integration Strategy)

我们将把 `ship` 的能力作为 MCP Tools 和 Resources 暴露出来，与现有的 REST API 并存。这使得 LLM Agent 能够原生发现和使用这些工具。

### 3.1 架构: FastAPI + MCP over SSE
我们将使用 `mcp` Python 包在 FastAPI 应用内创建一个 MCP Server。

- **传输协议**: Server-Sent Events (SSE) 用于服务器到客户端的消息，HTTP POST 用于客户端到服务器的消息。
- **端点**:
    - `GET /mcp/sse`: 建立 SSE 连接。
    - `POST /mcp/messages`: 发送 JSON-RPC 消息。

### 3.2 MCP Tools 映射
我们将现有的内部函数映射为 MCP Tools。

| 能力领域 | MCP Tool 名称 | 描述 | 底层函数 |
| :--- | :--- | :--- | :--- |
| **文件系统** | `fs_read_file` | 读取文件内容 | `filesystem.read_file` |
| | `fs_write_file` | 写入文件内容 (覆盖) | `filesystem.write_file` |
| | `fs_create_file` | 创建新文件 | `filesystem.create_file` |
| | `fs_edit_file` | 编辑文件 (字符串替换) | `filesystem.edit_file` |
| | `fs_list_dir` | 列出目录内容 | `filesystem.list_directory` |
| | `fs_delete` | 删除文件或目录 | `filesystem.delete_file` |
| **Shell** | `shell_exec` | 执行 Shell 命令 | `shell.execute_shell_command` |
| **IPython** | `python_exec` | 执行 Python 代码 | `ipython.execute_code` |

### 3.3 MCP Resources
我们可以将 workspace 暴露为 MCP Resource 层级，允许 LLM “订阅”文件变更或直接读取。
- **Resource URI**: `file:///workspace/{path}`
- **MIME Type**: 自动检测或 `text/plain`。

## 4. Agent 协议与能力发现 (Agent Protocol)

“Agent 协议”定义了 `ship` 容器如何向外部世界 (Manager) 宣告自身存在及能力。

### 4.1 能力清单 (Capability Manifest)
容器将在 `GET /capabilities` 端点暴露标准化的 JSON 清单。

```json
{
  "version": "1.0.0",
  "agent_id": "ship-container-xyz", // 实例 ID
  "capabilities": {
    "filesystem": true,
    "shell": true,
    "python": {
      "version": "3.12",
      "kernel": "ipython"
    },
    "browser": false,  // 如果启用则为 true
    "mcp": {
      "enabled": true,
      "endpoint": "/mcp/sse"
    }
  }
}
```

### 4.2 发现流程
1. Manager 启动 `ship` 容器。
2. Manager 轮询 `GET /health` 直到服务就绪。
3. Manager 请求 `GET /capabilities` 以了解可用工具。
4. Manager 连接 MCP 端点 `/mcp/sse` 将工具加载到 Agent 上下文中。

## 5. 浏览器能力 (Browser Capability - Playwright)

为了支持浏览器自动化，我们将集成 Playwright。

### 5.1 实现方式
- **Headless 模式**: 在 Headless 模式下运行 Playwright。
- **MCP Tools**: 将 Playwright 的操作暴露为 MCP Tools。

| MCP Tool 名称 | 参数 | 描述 |
| :--- | :--- | :--- |
| `browser_navigate` | `url: str` | 导航到指定 URL |
| `browser_click` | `selector: str` | 点击匹配选择器的元素 |
| `browser_type` | `selector: str`, `text: str` | 在匹配元素中输入文本 |
| `browser_screenshot` | `full_page: bool` | 截图 (返回 Base64 或 Image Resource) |
| `browser_get_content` | `format: "html" \| "text" \| "markdown"` | 获取页面内容 |
| `browser_eval` | `script: str` | 在页面上下文中执行 JavaScript |

### 5.2 依赖
- 在 `requirements.txt` 中添加 `playwright`。
- 在 `Dockerfile` 中安装浏览器 (`playwright install --with-deps chromium`)。

### 5.3 组件设计
创建 `pkgs/ship/app/components/browser.py` 来管理 Playwright 实例（单例 BrowserContext）并暴露 Router 和 MCP Tools。

## 6. 实施路线图 (Roadmap)

1.  **重构 (Refactor)**: 重命名 `user_manager` -> `process_manager`。
2.  **MCP 核心 (MCP Core)**: 添加 `mcp` 依赖，并在 `main.py` 中设置 SSE 传输层。
3.  **工具注册 (Tool Registration)**: 创建 `mcp_server.py`，将现有组件注册为 MCP Tools。
4.  **浏览器 (Browser)**: 添加 Playwright 支持并注册其工具。
5.  **能力接口 (Capabilities)**: 实现 `GET /capabilities`。
