# Shipyard Session-First 重构变更文档

## 概述

本次重构实现了三个核心功能：
1. **1:1 Session-Ship 绑定** - 简化架构，每个会话独占一个容器
2. **Execution History** - 记录执行历史，支持 Agent 技能库构建
3. **MCP Server 集成** - 提供 stdio 传输的 MCP 服务器

## 变更目的

### 1. 1:1 Session-Ship 绑定

**问题背景：**
- 原设计支持多个会话共享一个 Ship（容器），通过 `max_session_num` 控制
- 多会话共享增加了状态管理复杂度和潜在的隔离问题
- Agent 场景下，每个任务需要独立的环境

**解决方案：**
- 移除多会话共享逻辑，每个 Session 绑定一个专属 Ship
- 引入 Warm Pool（预热池）弥补冷启动延迟

### 2. Execution History（执行历史）

**问题背景：**
- 受 VOYAGER 论文启发，Agent 需要记录成功的执行路径来构建技能库
- 技能自我进化需要：代码/命令 + 成功状态 + 执行时间

**解决方案：**
- Bay 侧存储执行历史（对 Ship 透明）
- 记录 Python 和 Shell 执行的完整信息
- 提供查询 API 支持技能库构建

### 3. MCP Server 集成

**问题背景：**
- MCP (Model Context Protocol) 已成为行业标准（2025年12月捐赠给 Linux Foundation）
- OpenAI (2025年3月)、Google DeepMind (2025年4月) 均已采用
- Claude Desktop、ChatGPT Desktop、Cursor、VS Code 等工具支持 MCP 协议

**解决方案：**
- 使用官方 MCP Python SDK (`mcp` 包) 实现标准 MCP 服务器
- 支持 stdio 和 streamable-http 两种传输方式
- 提供 Python 执行、Shell 执行、文件操作等工具
- 发布 npm 包 `shipyard-mcp` 用于快速安装

### 4. SDK 架构重构

**问题背景：**
- 原 SDK (`ShipyardClient`) 暴露了过多底层细节
- MCP Server 和 SDK 代码重复
- 需要更简洁的接口给开发者使用

**解决方案：**
- 新增统一 `Sandbox` 类作为主要入口
- MCP Server 内部使用 SDK，避免代码重复
- 保留 `ShipyardClient` 作为低级 API

---

## 详细变更

### Bay 服务端

#### 模型变更 (`pkgs/bay/app/models.py`)

**移除字段：**
```python
# Ship 模型
- max_session_num: int = Field(default=1)
- current_session_num: int = Field(default=0)
```

**新增模型：**
```python
class ExecutionHistory(SQLModel, table=True):
    """执行历史记录，用于 Agent 技能库构建"""
    id: str                    # 主键
    session_id: str            # 关联的会话 ID
    ship_id: str               # 执行的 Ship ID
    exec_type: str             # 'python' 或 'shell'
    code: str                  # 执行的代码/命令
    success: bool              # 是否成功
    execution_time_ms: int     # 执行耗时（毫秒）
    output: Optional[str]      # 输出（可选存储）
    error: Optional[str]       # 错误信息
    created_at: datetime       # 创建时间

class ExecutionHistoryEntry(BaseModel):
    """API 响应模型"""
    id: str
    exec_type: str
    code: str
    success: bool
    execution_time_ms: int
    created_at: datetime

class ExecutionHistoryResponse(BaseModel):
    """执行历史查询响应"""
    entries: List[ExecutionHistoryEntry]
    total: int
```

#### 数据库服务 (`pkgs/bay/app/database.py`)

**移除方法：**
- `find_available_ship()` - 查找可用 Ship（多会话共享逻辑）
- `increment_ship_session_count()` - 增加会话计数
- `decrement_ship_session_count()` - 减少会话计数

**新增方法：**
```python
async def find_ship_for_session(session_id: str) -> Optional[Ship]
    """查找会话已绑定的 Ship（1:1 绑定）"""

async def find_warm_pool_ship() -> Optional[Ship]
    """从预热池获取可用 Ship"""

async def count_warm_pool_ships() -> int
    """统计预热池中的 Ship 数量"""

async def create_execution_history(
    session_id: str,
    ship_id: str,
    exec_type: str,
    code: str,
    success: bool,
    execution_time_ms: int,
    output: Optional[str] = None,
    error: Optional[str] = None,
) -> ExecutionHistory
    """创建执行历史记录"""

async def get_execution_history(
    session_id: str,
    exec_type: Optional[str] = None,
    success_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[List[ExecutionHistory], int]
    """查询执行历史"""
```

#### Ship 服务 (`pkgs/bay/app/services/ship/service.py`)

**简化逻辑：**
- `create_ship()` - 移除多会话分配逻辑，实现 1:1 绑定
- 优先复用会话已绑定的 Ship
- 其次从 Warm Pool 分配
- 最后创建新容器

**新增 Warm Pool 功能：**
```python
async def start_warm_pool()
    """启动预热池后台任务"""

async def stop_warm_pool()
    """停止预热池"""

async def _replenish_warm_pool()
    """补充预热池到目标数量"""

async def _create_warm_pool_ship() -> Ship
    """创建预热池 Ship"""

async def _assign_ship_to_session(ship: Ship, session_id: str, ttl: int)
    """将 Ship 分配给会话"""
```

**执行历史记录：**
- `execute_operation()` 中添加执行历史记录逻辑

#### 配置 (`pkgs/bay/app/config.py`)

**新增配置项：**
```python
warm_pool_enabled: bool = True           # 是否启用预热池
warm_pool_min_size: int = 2              # 最小预热数量
warm_pool_max_size: int = 10             # 最大预热数量
warm_pool_replenish_interval: int = 30   # 补充检查间隔（秒）
```

#### 路由 (`pkgs/bay/app/routes/sessions.py`)

**新增端点：**
```python
@router.get("/sessions/{session_id}/history")
async def get_execution_history(
    session_id: str,
    exec_type: Optional[str] = None,
    success_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> ExecutionHistoryResponse
    """获取会话执行历史"""
```

---

### Ship 容器端

#### IPython 组件 (`pkgs/ship/app/components/ipython.py`)

**ExecuteCodeResponse 新增字段：**
```python
code: str              # 执行的代码
execution_time_ms: int # 执行耗时（毫秒）
```

#### Shell 组件 (`pkgs/ship/app/components/shell.py`)

**ExecuteShellResponse 新增字段：**
```python
command: str           # 执行的命令
execution_time_ms: int # 执行耗时（毫秒）
```

#### 用户管理器 (`pkgs/ship/app/components/user_manager.py`)

**ProcessResult 新增字段：**
```python
command: str           # 执行的命令
execution_time_ms: int # 执行耗时（毫秒）
```

---

### Python SDK

#### 新增 Sandbox 类 (`shipyard_python_sdk/shipyard/sandbox.py`)

**统一入口类：**
```python
class Sandbox:
    """
    简化的沙箱接口，连接 Bay 服务执行代码。

    Usage:
        async with Sandbox() as sandbox:
            result = await sandbox.python.exec("print('hello')")
            print(result.stdout)
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,  # Bay API URL
        token: Optional[str] = None,      # 访问令牌
        ttl: int = 3600,                  # 会话 TTL
        session_id: Optional[str] = None, # 会话 ID（自动生成）
    )

    # 组件接口
    python: PythonExecutor   # sandbox.python.exec(code)
    shell: ShellExecutor     # sandbox.shell.exec(command)
    fs: FileSystem           # sandbox.fs.read/write/list

    # 方法
    async def start() -> Sandbox
    async def stop() -> None
    async def extend_ttl(ttl: int) -> None
    async def get_execution_history(...) -> Dict
```

**执行结果类：**
```python
@dataclass
class ExecResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    result: Any = None
    exit_code: int = 0
    execution_time_ms: int = 0
    code: str = ""
```

**便捷函数：**
```python
async def run_python(code: str, **kwargs) -> ExecResult
async def run_shell(command: str, **kwargs) -> ExecResult
```

#### Client (`shipyard_python_sdk/shipyard/client.py`)

**变更方法：**
```python
async def create_ship(
    ttl: int,
    spec: Optional[Spec] = None,
    max_session_num: int | None = None,  # 已弃用，添加警告
    session_id: Optional[str] = None,
    force_create: bool = False,
) -> SessionShip
```

**新增方法：**
```python
async def get_or_create_session(
    session_id: str,
    ttl: int = 3600,
    spec: Optional[Spec] = None,
) -> SessionShip
    """推荐的 Session-First API"""

def session(
    session_id: str,
    ttl: int = 3600,
    spec: Optional[Spec] = None,
) -> SessionContext
    """上下文管理器方式使用会话"""

async def get_execution_history(
    session_id: str,
    exec_type: Optional[str] = None,
    success_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]
    """获取执行历史"""
```

**新增类：**
```python
class SessionContext:
    """会话上下文管理器"""
    async def __aenter__(self) -> SessionShip
    async def __aexit__(self, ...)
```

#### Session (`shipyard_python_sdk/shipyard/session.py`)

**新增方法：**
```python
async def get_execution_history(
    exec_type: Optional[str] = None,
    success_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]
    """获取当前会话的执行历史"""

@property
def session_id(self) -> str
    """获取会话 ID"""
```

#### Types (`shipyard_python_sdk/shipyard/types.py`)

**移除属性：**
- `max_session_num`
- `current_session_num`

**新增属性：**
- `expires_at`

#### Utils (`shipyard_python_sdk/shipyard/utils.py`)

**更新函数签名：**
```python
async def create_session_ship(
    ttl: int = 3600,
    spec: Optional[Spec] = None,
    max_session_num: int | None = None,  # 已弃用
    endpoint_url: Optional[str] = None,
    access_token: Optional[str] = None,
    session_id: Optional[str] = None,
    force_create: bool = False,
) -> SessionShip
```

---

### MCP Server（新增）

#### 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         Agent / LLM                             │
└───────────────────────────────┬─────────────────────────────────┘
                                │
              ┌─────────────────┴─────────────────┐
              │                                   │
              ▼                                   ▼
    ┌─────────────────┐                 ┌─────────────────┐
    │   MCP Protocol  │                 │  开发者代码      │
    │ (Claude/Cursor) │                 │  (自研 Agent)   │
    └────────┬────────┘                 └────────┬────────┘
             │                                   │
             ▼                                   ▼
    ┌─────────────────┐                 ┌─────────────────┐
    │   MCP Server    │────────────────►│      SDK        │
    │                 │  内部使用 SDK    │    Sandbox      │
    └────────┬────────┘                 └────────┬────────┘
             │                                   │
             └───────────────┬───────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │       Bay       │
                    │  - Pool 管理     │
                    │  - Session 状态  │
                    │  - 执行历史      │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │      Ship       │
                    │  (Python/Shell) │
                    └─────────────────┘
```

#### 组件职责

| 组件 | 职责 |
|------|------|
| **MCP Server** | 规范化输入输出，让 MCP 客户端能调用沙箱 |
| **SDK (Sandbox)** | Python 开发者构建 Agent 时使用 |
| **Bay** | 容器池管理、Session 状态、执行历史 |
| **Ship** | 实际执行 Python/Shell |

#### 文件结构

**Bay 内置 MCP Server（使用 FastMCP）：**
```
pkgs/bay/app/mcp/
├── __init__.py
├── server.py      # MCP 服务器，内部使用 SDK Sandbox 类
└── run.py         # 入口点
```

**npm 包独立 MCP Server：**
```
pkgs/mcp-server/
├── bin/
│   └── shipyard-mcp.js   # Node.js CLI 入口
├── python/
│   ├── __init__.py
│   ├── __main__.py
│   └── server.py         # 独立 Python MCP 服务器（内置精简 SDK）
├── package.json
└── README.md
```

#### 提供的 MCP 工具

| 工具 | 描述 |
|------|------|
| `execute_python` | 执行 Python 代码 |
| `execute_shell` | 执行 Shell 命令 |
| `read_file` | 读取文件内容 |
| `write_file` | 写入文件 |
| `list_files` | 列出目录内容 |
| `install_package` | 通过 pip 安装包 |
| `get_sandbox_info` | 获取沙箱信息 |
| `get_execution_history` | 获取执行历史 |

#### 使用方式
```bash
# 环境变量
export SHIPYARD_ENDPOINT=http://localhost:8156
export SHIPYARD_TOKEN=secret-token

# 运行
python -m app.mcp.run
```

---

### Dashboard 前端

#### 类型定义 (`types/api.ts`)

**移除字段：**
- `max_session_num`
- `current_session_num`

#### 创建 Ship 表单 (`views/ship-create/useCreateShip.ts`)

- 移除 `maxSessionNum` 表单字段

#### Ship 详情页 (`views/ship-detail/index.vue`)

- 移除会话计数显示

#### Ship 列表页 (`views/ships/index.vue`)

- 移除会话数量列

---

### 单元测试

#### `pkgs/bay/tests/unit/test_ships.py`

**移除测试：**
- `test_create_ship_with_max_session_num`
- `test_find_available_ship`
- `test_session_count_increment_decrement`

**新增测试：**
- `test_execution_history_creation`
- `test_execution_history_query`
- `test_warm_pool_ship_allocation`

---

## SDK 使用示例

### 新方式：Sandbox 类（推荐）

**基本使用：**
```python
from shipyard import Sandbox

async with Sandbox() as sandbox:
    result = await sandbox.python.exec("print('hello')")
    print(result.stdout)  # hello
```

**自定义配置：**
```python
from shipyard import Sandbox

async with Sandbox(
    endpoint="http://bay.example.com:8156",
    token="your-token",
    ttl=7200,
    session_id="my-session-123"
) as sandbox:
    # Python 执行
    result = await sandbox.python.exec("import pandas; print(pandas.__version__)")

    # Shell 执行
    result = await sandbox.shell.exec("ls -la")

    # 文件操作
    await sandbox.fs.write("/workspace/test.py", "print('hello')")
    content = await sandbox.fs.read("/workspace/test.py")

    # 执行历史
    history = await sandbox.get_execution_history(success_only=True)
```

**一行代码：**
```python
from shipyard import run_python, run_shell

result = await run_python("print('hello')")
result = await run_shell("ls -la")
```

### 旧方式：ShipyardClient（仍支持）

```python
# 低级 API，提供更多控制
from shipyard import ShipyardClient

client = ShipyardClient(endpoint_url, access_token)
ship = await client.create_ship(ttl=3600)
result = await ship.python.exec("print('hello')")
```

### 新方式（推荐）

**方式一：get_or_create_session**
```python
session = await client.get_or_create_session(
    session_id="my-task-123",
    ttl=3600
)
result = await session.python.exec("print('hello')")

# 获取执行历史（用于技能库）
history = await session.get_execution_history(success_only=True)
```

**方式二：上下文管理器**
```python
async with client.session("my-task-123") as session:
    result = await session.python.exec("print('hello')")
    # 会话结束后资源由 TTL 管理
```

**方式三：便捷函数**
```python
from shipyard import create_session_ship

session = await create_session_ship(
    session_id="my-task-123",
    ttl=3600
)
```

---

## 技能库构建示例

基于 VOYAGER 论文思想，使用执行历史构建技能库：

```python
# 获取成功的 Python 执行记录
history = await session.get_execution_history(
    exec_type="python",
    success_only=True
)

# 筛选高效代码（执行时间短）
efficient_skills = [
    entry for entry in history["entries"]
    if entry["execution_time_ms"] < 1000
]

# 存入技能库
for skill in efficient_skills:
    skill_library.add(
        code=skill["code"],
        execution_time_ms=skill["execution_time_ms"],
        created_at=skill["created_at"]
    )
```

---

## 迁移指南

### 1. 移除 max_session_num

如果代码中使用了 `max_session_num` 参数：
```python
# 旧代码（会产生弃用警告）
ship = await client.create_ship(ttl=3600, max_session_num=3)

# 新代码
session = await client.get_or_create_session(
    session_id="my-session",
    ttl=3600
)
```

### 2. 使用 Session-First API

```python
# 旧模式：Ship 优先
ship = await client.create_ship(ttl=3600)

# 新模式：Session 优先
session = await client.get_or_create_session("my-session", ttl=3600)
```

### 3. 利用执行历史

```python
# 记录自动进行，无需额外代码
result = await session.python.exec(code)

# 查询历史
history = await session.get_execution_history()
```

### 4. 使用 MCP Server

**方式一：npm 包安装**
```bash
# 全局安装
npm install -g shipyard-mcp

# 运行
SHIPYARD_TOKEN=your-token shipyard-mcp
```

**方式二：Python 模块运行**
```bash
cd pkgs/bay
pip install -e .
python -m app.mcp.run
```

**方式三：HTTP 模式部署**
```bash
shipyard-mcp --transport http --port 8000
```

### 5. 使用新 SDK Sandbox 类

```python
# 旧方式
from shipyard import ShipyardClient
client = ShipyardClient(endpoint, token)
ship = await client.create_ship(ttl=3600)
result = await ship.python.exec(code)

# 新方式（推荐）
from shipyard import Sandbox
async with Sandbox() as sandbox:
    result = await sandbox.python.exec(code)
```

---

## MCP 客户端配置

### Claude Desktop

`~/.config/claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "shipyard": {
      "command": "shipyard-mcp",
      "env": {
        "SHIPYARD_ENDPOINT": "http://localhost:8156",
        "SHIPYARD_TOKEN": "your-access-token"
      }
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "shipyard": {
      "command": "shipyard-mcp",
      "env": {
        "SHIPYARD_ENDPOINT": "http://localhost:8156",
        "SHIPYARD_TOKEN": "your-access-token"
      }
    }
  }
}
```

### ChatGPT Desktop / VS Code

参考 `pkgs/mcp-server/README.md` 获取详细配置说明。

---

## 参考文献

1. **VOYAGER** (2023) - "VOYAGER: An Open-Ended Embodied Agent with Large Language Models"
   - 技能库自动构建
   - 代码 + 成功状态 + 执行时间的记录模式

2. **Reflexion** (2023) - "Reflexion: Language Agents with Verbal Reinforcement Learning"
   - 执行反馈用于 Agent 自我改进

3. **LearnAct** (2024) - "LearnAct: Few-Shot Mobile App Testing"
   - 从执行历史中学习可复用技能

---

## MCP HTTP 模式多客户端 Session 隔离

### 问题背景

当 MCP Server 以 HTTP 模式（`--transport http`）部署时，多个客户端连接到同一个服务器进程。原有实现使用 `lifespan` 创建单一 Sandbox，导致所有客户端共享同一个 Session，存在：

1. **状态污染**: 不同客户端共享 Python 变量、文件系统
2. **安全风险**: 一个客户端可以访问/修改另一个客户端的数据
3. **资源冲突**: 包安装、文件操作相互影响

### 解决方案

使用 FastMCP 原生的 per-session state 机制实现客户端隔离：

**架构变更：**
```
# 旧架构（所有客户端共享）
lifespan 启动 → Sandbox (session-123, ship-456)
                ↑
客户端 A ──────┘
客户端 B ──────┘

# 新架构（每客户端独立）
lifespan → GlobalConfig (endpoint, token)

客户端 A (mcp-session-aaa) → ctx.get_state → Sandbox A (ship-111)
客户端 B (mcp-session-bbb) → ctx.get_state → Sandbox B (ship-222)
```

**核心实现：**
```python
@dataclass
class GlobalConfig:
    """全局配置，在 lifespan 中初始化"""
    endpoint: str
    token: str
    default_ttl: int = 1800  # 30 分钟

@asynccontextmanager
async def mcp_lifespan(server: FastMCP) -> AsyncIterator[GlobalConfig]:
    # 只存储配置，不创建 Sandbox
    yield GlobalConfig(endpoint=endpoint, token=token)

async def get_or_create_sandbox(ctx: Context) -> Sandbox:
    """获取或创建当前 MCP session 的 Sandbox"""
    sandbox = await ctx.get_state("sandbox")

    if sandbox is None:
        config = ctx.request_context.lifespan_context
        sandbox = Sandbox(
            endpoint=config.endpoint,
            token=config.token,
            session_id=ctx.session_id,  # 使用 MCP session ID
            ttl=config.default_ttl,
        )
        await sandbox.start()
        await ctx.set_state("sandbox", sandbox)
    else:
        # 续期 TTL
        await sandbox.extend_ttl(config.default_ttl)

    return sandbox
```

### Session 清理机制

- **TTL 自动续期**: 每次 tool 调用时自动续期（每 10 分钟）
- **过期自动清理**: 如果客户端断开且 TTL 到期，Bay 自动清理 Ship
- **失效重建**: 如果 Sandbox 已过期，自动创建新的

### 配置

新增环境变量：
- `SHIPYARD_SANDBOX_TTL`: Sandbox TTL 秒数（默认 1800，即 30 分钟）

### 兼容性

- **stdio 模式**: 无影响（一个进程 = 一个 session = 一个 Sandbox）
- **HTTP 模式**: 每个 MCP 客户端获得独立的 Sandbox
- **现有配置**: 无需修改

### 验证

HTTP 模式隔离测试：
```python
async def test_http_mode_isolation():
    """测试 HTTP 模式下多客户端隔离"""
    async with aiohttp.ClientSession() as client_a:
        async with aiohttp.ClientSession() as client_b:
            # A 设置变量
            await call_tool(client_a, "execute_python", {"code": "x = 123"})

            # B 看不到 A 的变量
            result = await call_tool(client_b, "execute_python", {"code": "print(x)"})
            assert "NameError" in result
```

---

## 技能库增强（Skill Library Enhancement）

### 问题背景

原有的执行历史功能仅提供基础的 `get_execution_history` 查询，无法满足 Agent 构建技能库的完整需求：

1. **执行记录不完整**: 无法精确获取单条执行的完整代码
2. **缺乏标注能力**: Agent 无法为执行记录添加描述、标签或笔记
3. **查询粒度粗**: 无法按标签或有无笔记过滤

### 解决方案

基于 VOYAGER、Reflexion、LearnAct 论文的需求分析，增强执行历史功能：

**核心原则**: Sandbox 是执行环境，提供完整的执行素材；Agent 负责分析和学习。

### 新增功能

#### 1. 增强执行返回值

`execute_python` 和 `execute_shell` 新增参数：

```python
@mcp.tool()
async def execute_python(
    code: str,
    timeout: int = 30,
    include_code: bool = False,    # 返回执行的代码和 execution_id
    description: str = None,       # 代码描述（存入执行历史）
    tags: str = None,              # 标签（逗号分隔）
) -> str:
    """执行 Python 代码

    当 include_code=True 时，返回格式：
    execution_id: abc-123
    Code:
    print('hello')

    Output:
    hello

    Execution time: 5ms
    """
```

#### 2. 精确查询工具

**get_execution**: 按 ID 查询单条记录
```python
@mcp.tool()
async def get_execution(execution_id: str) -> str:
    """获取指定 ID 的执行记录的完整信息"""
```

**get_last_execution**: 获取最近一次执行
```python
@mcp.tool()
async def get_last_execution(exec_type: str = None) -> str:
    """获取最近一次执行的完整记录，可按类型过滤"""
```

#### 3. 标注工具

**annotate_execution**: 为执行记录添加标注
```python
@mcp.tool()
async def annotate_execution(
    execution_id: str,
    description: str = None,  # 描述
    tags: str = None,         # 标签
    notes: str = None,        # Agent 笔记
) -> str:
    """为执行记录添加/更新元数据"""
```

#### 4. 增强查询

**get_execution_history** 新增过滤参数：
```python
@mcp.tool()
async def get_execution_history(
    exec_type: str = None,
    success_only: bool = False,
    limit: int = 50,
    tags: str = None,           # 按标签过滤（任意匹配）
    has_notes: bool = False,    # 只返回有笔记的
    has_description: bool = False,  # 只返回有描述的
) -> str:
```

### 数据模型变更

**ExecutionHistory 新增字段：**
```python
class ExecutionHistory(SQLModel, table=True):
    # ... 原有字段 ...
    description: Optional[str]  # 执行描述
    tags: Optional[str]         # 标签（逗号分隔）
    notes: Optional[str]        # Agent 笔记
```

**ExecutionHistoryEntry 同步更新：**
```python
class ExecutionHistoryEntry(BaseModel):
    # ... 原有字段 ...
    description: Optional[str] = None
    tags: Optional[str] = None
    notes: Optional[str] = None
```

### 数据库迁移

新增迁移脚本：`pkgs/bay/alembic/versions/001_add_execution_history_metadata.py`

```python
def upgrade():
    op.add_column('execution_history', sa.Column('description', sa.String()))
    op.add_column('execution_history', sa.Column('tags', sa.String()))
    op.add_column('execution_history', sa.Column('notes', sa.String()))
```

### API 变更

**新增端点：**
- `GET /sessions/{session_id}/history/{execution_id}` - 获取单条记录
- `GET /sessions/{session_id}/history/last` - 获取最近一条
- `PATCH /sessions/{session_id}/history/{execution_id}` - 更新标注

**更新端点：**
- `GET /sessions/{session_id}/history` - 新增 `tags`, `has_notes`, `has_description` 参数

### SDK 变更

**Sandbox 类新增方法：**
```python
async def get_execution(self, execution_id: str) -> Dict
async def get_last_execution(self, exec_type: str = None) -> Dict
async def annotate_execution(
    self,
    execution_id: str,
    description: str = None,
    tags: str = None,
    notes: str = None,
) -> Dict
```

**get_execution_history 新增参数：**
```python
async def get_execution_history(
    self,
    exec_type: str = None,
    success_only: bool = False,
    limit: int = 100,
    tags: str = None,           # 新增
    has_notes: bool = False,    # 新增
    has_description: bool = False,  # 新增
) -> Dict
```

**PythonExecutor/ShellExecutor 新增参数：**
```python
async def exec(
    self,
    code: str,
    timeout: int = 30,
    description: str = None,  # 新增
    tags: str = None,         # 新增
) -> ExecResult
```

**ExecResult 新增字段：**
```python
@dataclass
class ExecResult:
    # ... 原有字段 ...
    execution_id: Optional[str] = None  # 新增：用于精确查询
```

### 使用示例

**基础工作流：**
```python
# 1. 执行代码（自动记录）
result = await sandbox.python.exec(
    "import pandas as pd; df = pd.read_csv('data.csv')",
    description="加载数据文件",
    tags="data-processing,pandas"
)

# 2. 获取完整记录
entry = await sandbox.get_last_execution()
print(entry["code"])  # 完整代码

# 3. 添加笔记
await sandbox.annotate_execution(
    entry["id"],
    notes="这段代码可以复用于任何 CSV 文件加载"
)
```

**技能库构建：**
```python
# 获取所有带笔记的成功执行（这些是 Agent 认为有价值的）
history = await sandbox.get_execution_history(
    success_only=True,
    has_notes=True,
)

# 构建技能库
for entry in history["entries"]:
    skill_library.add(
        code=entry["code"],
        description=entry["description"],
        tags=entry["tags"].split(",") if entry["tags"] else [],
        notes=entry["notes"],
    )
```

**按标签检索：**
```python
# 获取所有数据处理相关的执行
history = await sandbox.get_execution_history(
    tags="data-processing,etl",  # 匹配任一标签
    success_only=True,
)
```

### MCP 工具列表（更新后）

| 工具 | 描述 | 新增参数 |
|------|------|----------|
| `execute_python` | 执行 Python 代码 | `include_code`, `description`, `tags` |
| `execute_shell` | 执行 Shell 命令 | `include_code`, `description`, `tags` |
| `get_execution` | 获取单条执行记录 | (新工具) |
| `get_last_execution` | 获取最近执行 | (新工具) |
| `annotate_execution` | 标注执行记录 | (新工具) |
| `get_execution_history` | 查询执行历史 | `tags`, `has_notes`, `has_description` |
| `read_file` | 读取文件 | - |
| `write_file` | 写入文件 | - |
| `list_files` | 列出目录 | - |
| `install_package` | 安装包 | - |
| `get_sandbox_info` | 获取沙箱信息 | - |

### 文件变更

| 文件 | 变更类型 |
|------|----------|
| `pkgs/bay/app/models.py` | 模型扩展 |
| `pkgs/bay/app/database.py` | 新增方法 |
| `pkgs/bay/app/routes/sessions.py` | 新增端点 |
| `pkgs/bay/app/services/ship/service.py` | 支持元数据 |
| `pkgs/bay/app/mcp/server.py` | 新增工具 |
| `pkgs/mcp-server/python/server.py` | 同步更新 |
| `shipyard_python_sdk/shipyard/sandbox.py` | SDK 更新 |
| `pkgs/bay/alembic/versions/001_*.py` | 数据库迁移 |
