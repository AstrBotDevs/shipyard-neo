# Shipyard Neo MCP Server

MCP (Model Context Protocol) 服务器，为 AI 代理提供安全沙箱执行环境。

## 功能

通过 MCP 协议暴露以下工具：

| 工具 | 描述 |
|:--|:--|
| `create_sandbox` | 创建新的沙箱环境 |
| `delete_sandbox` | 删除沙箱 |
| `execute_python` | 在沙箱中执行 Python 代码 |
| `execute_shell` | 在沙箱中执行 shell 命令 |
| `read_file` | 读取沙箱中的文件 |
| `write_file` | 写入文件到沙箱 |
| `list_files` | 列出沙箱目录内容 |
| `delete_file` | 删除沙箱中的文件或目录 |

## 安装

```bash
pip install shipyard-neo-mcp
```

或从源码安装：

```bash
cd shipyard-neo-mcp
pip install -e .
```

## 配置

### 环境变量

| 变量 | 描述 | 必需 |
|:--|:--|:--|
| `SHIPYARD_ENDPOINT_URL` | Bay API 端点 URL | ✅ |
| `SHIPYARD_ACCESS_TOKEN` | 认证令牌 | ✅ |
| `SHIPYARD_DEFAULT_PROFILE` | 默认 profile（默认: `python-default`） | ❌ |
| `SHIPYARD_DEFAULT_TTL` | 默认 TTL 秒数（默认: `3600`） | ❌ |

### MCP 配置

添加到你的 MCP 设置文件：

```json
{
  "mcpServers": {
    "shipyard-neo": {
      "command": "shipyard-mcp",
      "env": {
        "SHIPYARD_ENDPOINT_URL": "http://localhost:8000",
        "SHIPYARD_ACCESS_TOKEN": "your-access-token"
      }
    }
  }
}
```

或者使用 Python 模块方式：

```json
{
  "mcpServers": {
    "shipyard-neo": {
      "command": "python",
      "args": ["-m", "shipyard_neo_mcp"],
      "env": {
        "SHIPYARD_ENDPOINT_URL": "http://localhost:8000",
        "SHIPYARD_ACCESS_TOKEN": "your-access-token"
      }
    }
  }
}
```

## 使用示例

一旦 MCP 服务器运行，AI 代理可以：

1. **创建沙箱并执行代码**
   ```
   使用 create_sandbox 工具创建一个沙箱，然后用 execute_python 运行代码
   ```

2. **文件操作**
   ```
   使用 write_file 写入代码文件，然后用 execute_shell 运行
   ```

3. **多步骤工作流**
   ```
   创建沙箱 → 写入多个文件 → 执行命令 → 读取结果 → 删除沙箱
   ```

## 工具详情

### create_sandbox

创建新沙箱。

**参数：**
- `profile` (可选): Profile ID，默认 "python-default"
- `ttl` (可选): 生存时间（秒），默认 3600

**返回：** 沙箱 ID 和状态信息

### execute_python

在沙箱中执行 Python 代码。

**参数：**
- `sandbox_id`: 沙箱 ID
- `code`: 要执行的 Python 代码
- `timeout` (可选): 超时秒数，默认 30

**返回：** 执行结果（output, error, success）

### execute_shell

执行 shell 命令。

**参数：**
- `sandbox_id`: 沙箱 ID
- `command`: Shell 命令
- `cwd` (可选): 工作目录
- `timeout` (可选): 超时秒数，默认 30

**返回：** 命令输出和退出码

### read_file

读取沙箱中的文件。

**参数：**
- `sandbox_id`: 沙箱 ID
- `path`: 文件路径（相对于 /workspace）

**返回：** 文件内容

### write_file

写入文件到沙箱。

**参数：**
- `sandbox_id`: 沙箱 ID
- `path`: 文件路径
- `content`: 文件内容

**返回：** 成功确认

### list_files

列出目录内容。

**参数：**
- `sandbox_id`: 沙箱 ID
- `path` (可选): 目录路径，默认 "."

**返回：** 文件列表（名称、类型、大小）

### delete_file

删除文件或目录。

**参数：**
- `sandbox_id`: 沙箱 ID
- `path`: 要删除的路径

**返回：** 成功确认

### delete_sandbox

删除沙箱。

**参数：**
- `sandbox_id`: 沙箱 ID

**返回：** 成功确认

## 许可证

AGPL-3.0-or-later
