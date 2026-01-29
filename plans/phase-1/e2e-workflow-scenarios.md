# E2E Workflow Scenarios

本文档规划了用于端到端测试的真实用户组合工作流场景。这些场景旨在模拟用户从创建沙箱到最终销毁的完整交互过程，覆盖核心功能和边界情况。

---

## 场景 1: 交互式数据分析 (Jupyter 风格)

**用户画像**: 数据分析师，探索数据，快速试验。

**目标**: 验证多轮代码执行、文件上传/下载、以及 stop/resume 后的数据持久化。

### 架构行为说明

当调用 `stop` 时：
1.  **容器被停止** (`docker stop`)
2.  IPython Kernel 终止 → **变量丢失**
3.  Sandbox 状态变为 `idle`, `current_session_id` 设为 `None`
4.  **Workspace Volume 保留** → 文件仍在

当 stop 后再次执行代码时：
1.  `ensure_running()` 发现 `current_session_id is None`
2.  **创建新的 Session 记录**
3.  **创建新的 Docker 容器** (挂载同一 Workspace Volume)
4.  新的 IPython Kernel 启动

> **设计意图**: `stop` 意味着"释放计算资源"，每次 resume 都是全新的容器。这提供了干净的运行环境，避免旧容器的潜在状态问题。文件通过 Workspace Volume 持久化，变量/内存状态不持久化。

### 行为序列

1.  **创建沙箱**: `POST /v1/sandboxes` -> 获取 `sandbox_id`, 状态 `idle`
2.  **上传数据文件**: `POST /filesystem/upload` 上传 `sales.csv`
3.  **执行代码 (第1轮)**: `POST /python/exec` - `import pandas as pd; df = pd.read_csv('sales.csv'); df.head()` -> 返回表格前几行
4.  **执行代码 (第2轮)**: `POST /python/exec` - `df['revenue'].sum()` -> 返回总和 (变量 `df` 在同一 Session 内保持)
5.  **执行代码 (第3轮)**: `POST /python/exec` - `import matplotlib.pyplot as plt; df['revenue'].plot(); plt.savefig('chart.png')` -> 生成图片文件
6.  **下载结果文件**: `GET /filesystem/download?path=chart.png` -> 获取 PNG 图片二进制内容
7.  **停止沙箱**: `POST /sandboxes/{id}/stop` -> 状态变为 `idle`，容器停止，Kernel 终止，但 Workspace Volume 保留
8.  **(用户离开几小时...)**
9.  **恢复执行 (尝试访问旧变量)**: `POST /python/exec` - `df.head()` -> **失败**: `NameError: name 'df' is not defined` (新容器，新 Kernel，变量丢失)
10. **恢复执行 (重新加载数据)**: `POST /python/exec` - `import pandas as pd; df = pd.read_csv('sales.csv'); df.head()` -> **成功**: 文件 `sales.csv` 仍然存在
11. **验证持久化文件**: `GET /filesystem/download?path=chart.png` -> **成功**: 之前生成的图片仍然存在
12. **删除沙箱**: `DELETE /v1/sandboxes/{id}` -> 204 No Content

### 测试要点

| # | 要点 | 预期行为 |
|---|------|----------|
| 1 | 多轮 `python/exec` | 变量在同一 Session 内保持 (步骤 3, 4) |
| 2 | `stop` 后 `resume` | 新容器、新 Kernel，变量丢失 (步骤 9) |
| 3 | 文件持久化 | `sales.csv` 和 `chart.png` 在 `stop/resume` 后仍然存在 (步骤 10, 11) |
| 4 | `download` 二进制文件 | 能正确下载图片文件 (步骤 6, 11) |


---

## 场景 2: 脚本开发与调试 (IDE 风格)

**用户画像**: 开发者，编写和调试 Python 脚本。

**目标**: 验证文件的创建、修改、覆盖写入，以及执行失败时的错误处理。

### 架构行为说明

1.  **首次文件操作触发容器启动**: `PUT /filesystem/files` 会调用 `ensure_running()` 启动容器
2.  **文件覆盖**: 使用 `mode="w"`，同路径文件会被完全覆盖
3.  **执行失败时**: `success=false`, `error` 字段包含完整的 Python traceback

### 行为序列

1.  **创建沙箱**: `POST /v1/sandboxes` -> 获取 `sandbox_id`
2.  **写入脚本 (版本1, 有 Bug)**: `PUT /filesystem/files` - path: `script.py`, content: `print(1/0)` (故意除零错误)
    - 首次文件操作触发容器启动
3.  **执行脚本**: `POST /python/exec` - `exec(open('script.py').read())` 
    - 返回 `success=false`
    - `error` 包含 `ZeroDivisionError: division by zero` traceback
    - `output` 为空
4.  **修改脚本 (版本2, 修复 Bug)**: `PUT /filesystem/files` - path: `script.py`, content: `print('Hello, World!')` (覆盖写入)
5.  **执行脚本**: `POST /python/exec` - `exec(open('script.py').read())` 
    - 返回 `success=true`
    - `output`: `Hello, World!\n`
6.  **读取脚本内容**: `GET /filesystem/files?path=script.py` -> 返回版本2的内容
7.  **删除沙箱**: `DELETE /v1/sandboxes/{id}` -> 204

### 测试要点

| # | 要点 | 预期行为 |
|---|------|----------|
| 1 | 文件覆盖写入 | `PUT` 同一路径会更新文件内容 (步骤 4) |
| 2 | 执行失败 | `python/exec` 返回 `success=false`, `error` 包含 traceback (步骤 3) |
| 3 | 执行成功后输出 | `output` 包含 print 输出 (步骤 5) |
| 4 | 读取文件 | `GET /filesystem/files` 返回最新内容 (步骤 6) |

---

## 场景 3: 项目初始化与依赖安装 (代码仓库风格)

**用户画像**: 软件工程师，设置一个项目环境。

**目标**: 验证多文件/嵌套目录创建，以及**依赖安装的持久化边界**。

### 架构行为说明

1.  **嵌套目录自动创建**: Ship 的 `/fs/write_file` 会自动创建父目录 (`mkdir -p` 语义)
2.  **依赖持久化边界**:
    - **持久化**: 安装到 `/workspace/` 内 (如 `pip install --target /workspace/.libs`)
    - **不持久化**: 标准 `pip install` 安装到容器系统目录，stop 后丢失
3.  **新容器挂载同一 Volume**: stop/resume 后，新容器挂载相同的 Workspace Volume，文件和 `--target` 安装的库都保留

### 行为序列

1.  **创建沙箱**: `POST /v1/sandboxes` -> 获取 `sandbox_id`
2.  **写入依赖文件**: `PUT /filesystem/files` - path: `requirements.txt`, content: `requests==2.31.0`
3.  **写入代码到嵌套目录**: `PUT /filesystem/files` - path: `src/main.py`, content: `import requests; print(requests.__version__)`
    - Ship 自动创建 `src/` 目录
4.  **安装依赖到 Workspace 内**: `POST /python/exec` - `import subprocess; subprocess.run(['pip', 'install', '-r', 'requirements.txt', '--target', '/workspace/.libs'], check=True)`
    - 库安装到 `/workspace/.libs/` (Volume 内，会持久化)
5.  **执行代码 (使用安装的库)**: `POST /python/exec` - `import sys; sys.path.insert(0, '/workspace/.libs'); exec(open('src/main.py').read())`
    - 输出 `2.31.0`
6.  **停止沙箱**: `POST /sandboxes/{id}/stop`
    - 容器停止，Kernel 终止
    - Workspace Volume 保留 (包含 `requirements.txt`, `src/main.py`, `.libs/`)
7.  **恢复执行 (验证依赖持久化)**: `POST /python/exec` - `import sys; sys.path.insert(0, '/workspace/.libs'); import requests; print(requests.__version__)`
    - **新容器**挂载同一 Workspace Volume
    - `.libs/` 仍存在
    - 输出 `2.31.0`
8.  **删除沙箱**: `DELETE /v1/sandboxes/{id}` -> 204

### 测试要点

| # | 要点 | 预期行为 |
|---|------|----------|
| 1 | 嵌套目录 | `PUT` 到 `src/main.py` 时自动创建 `src/` 目录 (步骤 3) |
| 2 | 依赖持久化 (使用 `--target`) | `pip install --target /workspace/.libs` 安装的库在 stop/resume 后仍可用 (步骤 7) |

> **重要**: 标准 `pip install` 安装到容器系统目录，**不会持久化**。如果需要持久化依赖，必须使用 `--target /workspace/.libs` 或在 `/workspace` 内创建虚拟环境。

---

## 场景 4: 简单快速执行 (无状态 Serverless 风格)

**用户画像**: 用户（或自动化系统）只想快速执行一段代码，不关心持久化。

**目标**: 验证最小路径下的创建->执行->删除流程。

### 架构行为说明

1.  **懒加载**: `POST /v1/sandboxes` 只创建 Sandbox 记录和 Workspace Volume，**不启动容器**
2.  **冷启动**: 首次 `python/exec` 触发完整启动流程:
    - 创建 Session → 创建容器 → 启动容器 → 等待 Ship 就绪 → 执行代码
3.  **冷启动延迟**: 
    - 镜像已缓存: 2-5 秒
    - 需要拉取镜像: 30-120+ 秒
4.  **完全清理**: `DELETE` 会删除容器和 Volume

### 行为序列

1.  **创建沙箱**: `POST /v1/sandboxes` -> 获取 `sandbox_id`
    - 状态: `idle`
    - **容器未启动**
2.  **执行代码**: `POST /python/exec` - `print(2 * 21)`
    - 触发 `ensure_running()` → 创建并启动容器
    - 等待 Ship 就绪 (冷启动延迟)
    - 执行代码
    - 返回 `success=true`, output: `42\n`
3.  **删除沙箱**: `DELETE /v1/sandboxes/{id}` -> 204
    - 容器被删除
    - Workspace Volume 被删除
    - 后续 GET 返回 404

### 测试要点

| # | 要点 | 预期行为 |
|---|------|----------|
| 1 | 最小路径 | 3 个 API 调用完成完整生命周期 |
| 2 | 懒加载 | 创建时不启动容器，执行时才启动 |
| 3 | 冷启动 | 首次执行有延迟，但应在合理时间内完成 |
| 4 | 完全清理 | 删除后 GET 返回 404 |

---

## 下一步

- [ ] 确认场景优先级
- [ ] 将场景转化为 `test_e2e_api.py` 中的测试用例
- [ ] 运行测试并修复发现的问题
