# Ship - Containerized Execution Environment

Ship 是一个基于 FastAPI 的容器化执行环境，提供文件系统、IPython 内核和 Shell 命令执行功能。支持基于 Session ID 的工作目录隔离。

## 功能

- **基于 Session 的隔离**: 每个 Session ID 对应独立的工作目录 (`/workspace/{session_id}`)
- **文件系统操作**: 在隔离的工作目录中进行文件操作
- **IPython 内核**: 每个 Session 拥有独立的 Python 内核，工作目录已设置
- **Shell 命令执行**: 在隔离的环境中执行 Shell 命令

## API 接口

所有接口都需要 **`X-SESSION-ID`** 请求头来标识会话。

### 文件系统 (Filesystem)
- `POST /fs/create_file` - 在 Session 工作目录中创建文件
- `POST /fs/read_file` - 从 Session 工作目录中读取文件内容
- `POST /fs/write_file` - 写入文件内容到 Session 工作目录
- `POST /fs/delete_file` - 删除 Session 工作目录中的文件或目录
- `POST /fs/list_dir` - 列出 Session 工作目录中的内容

### IPython 内核
- `POST /ipython/exec` - 在 Session 的 Python 内核中执行代码
- `POST /ipython/create_kernel` - 为 Session 创建新的内核
- `DELETE /ipython/kernel` - 关闭 Session 的内核
- `GET /ipython/kernels` - 列出所有活跃内核
- `GET /ipython/kernel/status` - 获取 Session 内核状态

### Shell 命令执行
- `POST /shell/exec` - 在 Session 工作目录中执行 Shell 命令
- `GET /shell/processes` - 列出 Session 的后台进程
- `GET /shell/process/{process_id}` - 获取 Session 进程状态
- `DELETE /shell/process/{process_id}` - 终止 Session 进程
- `GET /shell/cwd` - 获取 Session 当前工作目录

## 安装和运行

### 使用 uv (推荐)

```bash
# 安装依赖
uv sync

# 启动服务
uv run python run.py
```

### 使用 pip

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python run.py
```

### 使用 Docker

```bash
# 构建镜像
docker build -t ship .

# 运行容器
docker run -p 8123:8123 -v /workspace:/workspace ship
```

## 测试

```bash
# 安装测试依赖
pip install aiohttp

# 运行测试
python test_ship.py
```

## 工作目录隔离

每个 Session ID 都会在 `/workspace/{session_id}/` 下创建独立的工作目录：

- 文件系统操作限制在该目录内
- IPython 内核启动时自动设置工作目录
- Shell 命令默认在该目录中执行
- 相对路径都相对于该工作目录解析

## 使用示例

```python
import aiohttp

async def example():
    headers = {"X-SESSION-ID": "my-session-123"}
    
    async with aiohttp.ClientSession() as session:
        # 创建文件
        await session.post("http://localhost:8123/fs/create_file", 
                          json={"path": "hello.txt", "content": "Hello World!"}, 
                          headers=headers)
        
        # 执行 Python 代码
        await session.post("http://localhost:8123/ipython/exec", 
                          json={"code": "print(open('hello.txt').read())"}, 
                          headers=headers)
        
        # 执行 Shell 命令
        await session.post("http://localhost:8123/shell/exec", 
                          json={"command": "ls -la"}, 
                          headers=headers)
```

## API 文档

启动服务后，访问 `http://localhost:8123/docs` 查看 Swagger API 文档。

## 项目结构

```
app/
├── __init__.py
├── main.py              # 主应用入口
└── components/
    ├── __init__.py
    ├── filesystem.py    # 文件系统组件 (支持 Session 隔离)
    ├── ipython.py      # IPython 内核组件 (支持 Session 隔离)
    └── shell.py        # Shell 执行组件 (支持 Session 隔离)
```