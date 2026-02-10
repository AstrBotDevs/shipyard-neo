# Phase 2 Progress（实施进度记录）

> 更新时间：2026-02-10

本文档记录 Phase 2 已完成/进行中/待完成事项，方便在实现过程中快速对齐。

---

## 0. 当前结论/约定

- 浏览器运行时 **统一命名为 `gull`**（`runtime_type: gull`），不再使用 `runtime_type: browser`。
- Browser Runtime 通过 **CLI passthrough** 暴露能力：Bay 调用 Gull 的 `POST /exec`，由 Gull 透传执行 `agent-browser`。
- **不单独暴露 screenshot capability**：通过 `agent-browser screenshot /workspace/xxx.png` 写入共享 Cargo Volume，再由 Ship 的 filesystem/download 拉回。
- 命令 **不带 `agent-browser` 前缀**：Gull 自动注入 `agent-browser`、`--session`、`--profile` 参数。
- **Agent 做编排，不做脚本注入**：Agent 比 bash 更擅长做决策（条件判断、错误恢复、动态元素处理）。
- **跨容器协调**：Gull（浏览器）和 Ship（Python/Shell）通过 Cargo Volume（`/workspace`）共享数据，Agent 在 MCP 层自然编排。

---

## 1. 已完成（Done）

### 1.1 Profile Schema V2（多容器配置）

- 实现 V2 配置模型（`ContainerSpec`/`StartupConfig`/`ProfileConfig` 兼容归一化）。
  - 代码：[`pkgs/bay/app/config.py`](pkgs/bay/app/config.py:1)
- 新增单测覆盖：legacy profile 自动归一化、多容器解析、primary_for 优先级、primary container 解析。
  - 测试：[`pkgs/bay/tests/unit/config/test_profile_v2.py`](pkgs/bay/tests/unit/config/test_profile_v2.py:1)

### 1.2 Session 模型（Phase 2 多容器字段 + 状态）

- 扩展 Session 模型：新增 `containers: JSON` 字段、增加 `DEGRADED` 状态，提供能力/endpoint 查询辅助方法。
  - 代码：[`pkgs/bay/app/models/session.py`](pkgs/bay/app/models/session.py:1)

### 1.3 现有 Session 启动逻辑对 V2 Profile 的兼容

- 让 `SessionManager` 不再依赖 legacy `profile.runtime_port/runtime_type` 直接字段，而是基于 `profile.get_primary_container()`。
  - 代码：[`pkgs/bay/app/managers/session/session.py`](pkgs/bay/app/managers/session/session.py:1)

### 1.4 DockerDriver（多容器编排 + 向后兼容）

- `DockerDriver.create()` 继续基于 `profile.get_primary_container()`，保持 Phase 1/2 兼容。
- 新增 Phase 2 多容器编排：Session-scoped network + 多容器 create/start/stop/destroy（失败全部回滚）。
  - 代码：[`pkgs/bay/app/drivers/docker/docker.py`](pkgs/bay/app/drivers/docker/docker.py:1)
  - 相关模型：[`pkgs/bay/app/drivers/base.py`](pkgs/bay/app/drivers/base.py:1)

### 1.5 Gull（浏览器运行时）包与镜像骨架

- 新增 Gull 包（uv 管理、可构建 wheel）：
  - 配置：[`pkgs/gull/pyproject.toml`](pkgs/gull/pyproject.toml:1)
  - 锁文件：[`pkgs/gull/uv.lock`](pkgs/gull/uv.lock:1)
- Gull FastAPI Thin Wrapper：
  - 入口：[`pkgs/gull/app/main.py`](pkgs/gull/app/main.py:1)
  - 端点：`POST /exec`、`POST /exec_batch`、`GET /health`、`GET /meta`
  - `agent-browser` 持久化：使用 `--profile /workspace/.browser/profile`（Cargo Volume）
  - 命令解析：使用 `shlex.split()`，支持带引号参数
- Gull Dockerfile（node + agent-browser + chromium deps + uv sync --frozen）：
  - 镜像：[`pkgs/gull/Dockerfile`](pkgs/gull/Dockerfile:1)
- Gull 单测（不依赖 agent-browser 实际安装）：
  - 测试：[`pkgs/gull/tests/test_runner.py`](pkgs/gull/tests/test_runner.py:1)

### 1.6 Bay 侧 GullAdapter 接入

- 新增 GullAdapter（HTTP adapter，调用 Gull `/meta`、`/exec`、`/exec_batch`）：
  - 代码：[`pkgs/bay/app/adapters/gull.py`](pkgs/bay/app/adapters/gull.py:1)
  - 导出：[`pkgs/bay/app/adapters/__init__.py`](pkgs/bay/app/adapters/__init__.py:1)
- BaseAdapter 新增 `exec_browser_batch()` 默认实现：
  - 代码：[`pkgs/bay/app/adapters/base.py`](pkgs/bay/app/adapters/base.py:97)
- CapabilityRouter 识别 `runtime_type == "gull"` + 新增 `exec_browser_batch()` 路由：
  - 代码：[`pkgs/bay/app/router/capability/capability.py`](pkgs/bay/app/router/capability/capability.py:269)
- GullAdapter 单测：
  - 测试：[`pkgs/bay/tests/unit/adapters/test_gull_adapter.py`](pkgs/bay/tests/unit/adapters/test_gull_adapter.py:1)

### 1.7 Bay API：浏览器 exec + exec_batch

- ✅ 单条执行：`POST /v1/capabilities/{sandbox_id}/browser/exec`
- ✅ 批量执行：`POST /v1/capabilities/{sandbox_id}/browser/exec_batch`
  - Execution History 记录为单条 `exec_type=browser_batch`
  - 代码：[`pkgs/bay/app/api/v1/capabilities.py`](pkgs/bay/app/api/v1/capabilities.py:337)
- Execution History `exec_type` 枚举新增 `BROWSER` 和 `BROWSER_BATCH`：
  - 代码：[`pkgs/bay/app/models/skill.py`](pkgs/bay/app/models/skill.py:23)

### 1.8 SDK：Browser Capability（exec + exec_batch）

- `BrowserCapability.exec()` — 单条浏览器命令执行
  - 代码：[`shipyard-neo-sdk/shipyard_neo/capabilities/browser.py`](shipyard-neo-sdk/shipyard_neo/capabilities/browser.py:1)
- `BrowserCapability.exec_batch()` — 批量浏览器命令执行
  - 代码：[`shipyard-neo-sdk/shipyard_neo/capabilities/browser.py`](shipyard-neo-sdk/shipyard_neo/capabilities/browser.py:46)
- 新增类型：`BrowserBatchStepResult`、`BrowserBatchExecResult`
  - 代码：[`shipyard-neo-sdk/shipyard_neo/types.py`](shipyard-neo-sdk/shipyard_neo/types.py:133)
- SDK 单测覆盖 `exec_batch`（成功 + 部分失败）：
  - 测试：[`shipyard-neo-sdk/tests/test_client.py`](shipyard-neo-sdk/tests/test_client.py:1)

### 1.9 MCP：浏览器工具（3 个新工具）

- `execute_browser` — 单条浏览器命令
  - 代码：[`shipyard-neo-mcp/src/shipyard_neo_mcp/server.py`](shipyard-neo-mcp/src/shipyard_neo_mcp/server.py:568)
- `execute_browser_batch` — 批量浏览器命令序列
  - 代码：[`shipyard-neo-mcp/src/shipyard_neo_mcp/server.py`](shipyard-neo-mcp/src/shipyard_neo_mcp/server.py:598)
- `list_profiles` — 列出可用沙箱 profile
  - 代码：[`shipyard-neo-mcp/src/shipyard_neo_mcp/server.py`](shipyard-neo-mcp/src/shipyard_neo_mcp/server.py:634)
- `_read_exec_type()` 扩展支持 `browser`/`browser_batch` 过滤：
  - 代码：[`shipyard-neo-mcp/src/shipyard_neo_mcp/server.py`](shipyard-neo-mcp/src/shipyard_neo_mcp/server.py:109)
- MCP 包管理修复：添加 `[tool.uv.sources]` 解决本地 `shipyard-neo-sdk` 依赖：
  - 配置：[`shipyard-neo-mcp/pyproject.toml`](shipyard-neo-mcp/pyproject.toml:1)
- MCP 单测覆盖（10 个新测试）：
  - 测试：[`shipyard-neo-mcp/tests/test_server.py`](shipyard-neo-mcp/tests/test_server.py:1)

### 1.10 文档同步（runtime_type 统一）

- 将 Phase 2 文档中的 `runtime_type: browser` 统一为 `runtime_type: gull`：
  - [`plans/phase-2/profile-schema-v2.md`](plans/phase-2/profile-schema-v2.md:1)
  - [`plans/phase-2/browser-integration-design.md`](plans/phase-2/browser-integration-design.md:1)
- 新增设计文档：
  - [`plans/phase-2/mcp-browser-skill-design.md`](plans/phase-2/mcp-browser-skill-design.md:1) — MCP 浏览器工具 + 操作技能设计（3 核心决策 + 3 阶段计划）
  - [`plans/phase-2/batch-execution-design.md`](plans/phase-2/batch-execution-design.md:1) — Batch 执行方案 4 层全栈技术规格

### 1.11 测试现状

| 包 | 测试数 | 状态 |
|---|--------|------|
| Bay unit tests | 301 passed | ✅ |
| Gull unit tests | 3 passed | ✅ |
| SDK tests | 27 passed | ✅ |
| MCP tests | 24 passed | ✅ |

---

## 2. 进行中（In Progress）

（当前无进行中事项。阶段 1-2 实现全部完成。）

---

## 3. 待完成（Todo / Next）

### 3.1 生命周期与异常策略

- 多容器创建失败：全部回滚（✅ 已在启动逻辑实现）
- 运行中某容器挂掉：Session 标记 `DEGRADED`，对应能力 503（待补：`refresh_status()` 多容器版 + GC/路由层对 DEGRADED 返回 503）
- idle 回收：全活跃计数（Session 级 last_activity）（已存在，但需要确认 browser exec 触发 touch）

### 3.2 集成测试 / E2E

- E2E：创建 `ship + gull` profile
  1) gull: open/snapshot/screenshot 写入 `/workspace`
  2) ship: 下载截图并用 python 读取图片尺寸

### 3.3 操作技能（Skill）— Phase 2 阶段 3

参见 [`plans/phase-2/mcp-browser-skill-design.md` 阶段 3](plans/phase-2/mcp-browser-skill-design.md:219)

| 步骤 | 内容 | 涉及文件 |
|------|------|---------|
| 3.1 | 创建 `skills/shipyard-sandbox/SKILL.md` | 新文件 |
| 3.2 | 创建浏览器参考 `references/browser.md` | 新文件（从 `skills/agent-browser/` 适配） |
| 3.3 | 创建技能生命周期参考 `references/skills-lifecycle.md` | 新文件 |
| 3.4 | 创建工具速查 `references/tools-reference.md` | 新文件 |

---

## 4. 运行方式（开发提示）

### Gull
- 安装依赖：`cd pkgs/gull && uv sync --group dev`
- 单测：`cd pkgs/gull && uv run python -m pytest -q`

### Bay
- 单测：`cd pkgs/bay && uv run python -m pytest tests/unit/ -q`

### SDK
- 安装依赖：`cd shipyard-neo-sdk && uv sync --extra dev`
- 单测：`cd shipyard-neo-sdk && uv run python -m pytest -q`

### MCP
- 安装依赖：`cd shipyard-neo-mcp && uv sync --extra dev`
- 单测：`cd shipyard-neo-mcp && uv run python -m pytest -q`
