# Bay Phase 1 当前进展（截至 2026-01-28）

> 本文记录我们今天把 Bay Phase 1 推进到了哪一步、已跑通的最小链路、以及后续必须补齐的工作项。
>
> 相关设计：
> - [`plans/bay-implementation-path.md`](plans/bay-implementation-path.md:1)
> - [`plans/bay-api.md`](plans/bay-api.md:1)
> - [`plans/bay-design.md`](plans/bay-design.md:1)

## 0. 今日达成（Summary）

### 0.1 Bay 工程骨架与核心分层已落地

已落地目录：[`pkgs/bay/`](pkgs/bay:1)

- FastAPI 入口：[`pkgs/bay/app/main.py`](pkgs/bay/app/main.py:1)
- 配置系统（支持 YAML + env 覆盖）：[`pkgs/bay/app/config.py`](pkgs/bay/app/config.py:1)
- DB（SQLite async）：[`pkgs/bay/app/db/session.py`](pkgs/bay/app/db/session.py:1)
- Models：
  - Sandbox：[`pkgs/bay/app/models/sandbox.py`](pkgs/bay/app/models/sandbox.py:1)
  - Workspace：[`pkgs/bay/app/models/workspace.py`](pkgs/bay/app/models/workspace.py:1)
  - Session：[`pkgs/bay/app/models/session.py`](pkgs/bay/app/models/session.py:1)
  - IdempotencyKey：[`pkgs/bay/app/models/idempotency.py`](pkgs/bay/app/models/idempotency.py:1)
- Driver 抽象 + DockerDriver：
  - Driver 接口：[`pkgs/bay/app/drivers/base.py`](pkgs/bay/app/drivers/base.py:1)
  - DockerDriver（支持 container_network/host_port/auto）：[`pkgs/bay/app/drivers/docker/docker.py`](pkgs/bay/app/drivers/docker/docker.py:1)
- Managers：
  - SandboxManager：[`pkgs/bay/app/managers/sandbox/sandbox.py`](pkgs/bay/app/managers/sandbox/sandbox.py:1)
  - SessionManager：[`pkgs/bay/app/managers/session/session.py`](pkgs/bay/app/managers/session/session.py:1)
  - WorkspaceManager：[`pkgs/bay/app/managers/workspace/workspace.py`](pkgs/bay/app/managers/workspace/workspace.py:1)
- RuntimeClient（Ship HTTP client）：[`pkgs/bay/app/clients/runtime/ship.py`](pkgs/bay/app/clients/runtime/ship.py:1)
- CapabilityRouter：[`pkgs/bay/app/router/capability/capability.py`](pkgs/bay/app/router/capability/capability.py:1)

### 0.2 Phase 1 最小 E2E 链路已跑通

已跑通链路（符合 [`plans/bay-implementation-path.md`](plans/bay-implementation-path.md:19) 的 0.2 验收思路）：

1. `POST /v1/sandboxes` 创建 sandbox（lazy session，初始 `status=idle`）
2. `POST /v1/sandboxes/{id}/python/exec`
   - Bay 触发 `ensure_running`
   - DockerDriver 创建并启动 ship 容器 + workspace volume 挂载
   - 通过 **host_port 端口映射** 获取 endpoint（`http://127.0.0.1:<HostPort>`）
   - 调用 ship `/ipython/exec` 成功返回

对应 API 入口：
- sandboxes：[`pkgs/bay/app/api/v1/sandboxes.py`](pkgs/bay/app/api/v1/sandboxes.py:1)
- capabilities：[`pkgs/bay/app/api/v1/capabilities.py`](pkgs/bay/app/api/v1/capabilities.py:1)

### 0.3 修复：首个 python/exec 请求不再需要客户端重试

现状：首个 `python/exec` 单次请求即可返回 200。
原因：在 Session 启动后增加了 runtime readiness 等待（容器启动 ≠ HTTP server ready）。

### 0.4 Ship 镜像本地已构建

- 已执行 `docker build -t ship:latest pkgs/ship`，可用于 Bay 直接拉起 runtime。

## 1. 当前可用的接口清单

### 1.1 Bay 自身
- `GET /health`
- `GET /v1/profiles`

### 1.2 Sandboxes（已可用）
- `POST /v1/sandboxes`
- `GET /v1/sandboxes`
- `GET /v1/sandboxes/{id}`
- `POST /v1/sandboxes/{id}/keepalive`
- `POST /v1/sandboxes/{id}/stop`
- `DELETE /v1/sandboxes/{id}`

### 1.3 Capabilities（已可用，但仍需补齐安全/校验/错误映射）
- `POST /v1/sandboxes/{id}/python/exec`
- `POST /v1/sandboxes/{id}/shell/exec`
- `POST /v1/sandboxes/{id}/files/read`
- `POST /v1/sandboxes/{id}/files/write`
- `POST /v1/sandboxes/{id}/files/list`
- `POST /v1/sandboxes/{id}/files/delete`

## 2. 当前运行默认配置（dev）

- [`pkgs/bay/config.yaml`](pkgs/bay/config.yaml:1)
  - 默认按“Bay 跑宿主机”方式连接：`connect_mode=host_port` + `publish_ports=true`
  - profile：仅保留 `python-default`
  - ship runtime_port：8123（与 ship 镜像启动日志一致）

## 3. Phase 1 还需要跑通/补齐的内容（Next）

> 这些是 Phase 1 要交付为“可上线雏形”必须补齐的，优先级从高到低。

### 3.1 必须补齐（P0）

1. **Ship `/meta` 握手校验接入**（对应 Milestone 4）
   - Bay 在 `ensure_running` 后调用 ship `GET /meta`
   - 校验：
     - `workspace.mount_path == /workspace`
     - `capabilities ⊇ profile.capabilities`
     - `api_version` 兼容

2. **统一错误模型落地**（对齐 [`plans/bay-api.md`](plans/bay-api.md:80)）
   - ship 错误 -> `ship_error` (502)
   - runtime not ready -> `session_not_ready` (503) + 可选 `Retry-After`

3. **幂等键 Idempotency-Key**（至少覆盖 `POST /v1/sandboxes`）
   - 表已建模：[`IdempotencyKey`](pkgs/bay/app/models/idempotency.py:1)
   - 但 API 层尚未接入

4. **stop/delete 语义与资源回收验证**
   - `stop`：只回收算力（destroy session/container），保留 workspace
   - `delete`：managed workspace 级联删除
   - 需要补 E2E 测试脚本验证容器/volume 是否被正确清理

### 3.2 建议补齐（P1）

1. **鉴权与 owner 隔离**
   - 目前 owner 通过 `X-Owner` 或默认 `default`（开发简化）
   - Phase 1 至少需要可替换的鉴权 middleware / dependency

2. **路径安全校验**
   - files API 需要严格拒绝绝对路径/`../`
   - ship 已有 `resolve_path`，Bay 侧也应做一层校验（防止绕过）

3. **可观测性**
   - request_id 贯穿
   - 关键路径日志与 metrics（可后置）

### 3.3 暂不做（明确不做）

- K8sDriver（Phase 2）
- 对外暴露 Session
- 多 sandbox 共享 workspace

## 4. 建议的下一步执行顺序

1. 接入 ship `GET /meta` 握手（先把校验失败映射成 `ship_error` 或 `validation_error`）
2. 把 `Idempotency-Key` 接入 `POST /v1/sandboxes`
3. 写一个最小 E2E 脚本（create -> python/exec -> stop -> delete）并校验资源回收
4. 再补 filesystem/shell 的边界与安全校验
