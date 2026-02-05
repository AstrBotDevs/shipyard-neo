# Phase 1.5 - Container Health Probing (主动探活) 设计说明

> **目标**：把“容器已死但 Bay 还认为 RUNNING”的状态差，收敛成可预测的 API 行为，并给出可执行的实现与测试计划。
> 
> **范围**：仅 Bay 侧（编排层）对 Session 容器状态的探测与恢复策略；不引入新依赖。

---

## 1. 背景与问题定义

### 1.1 当前行为（被动发现）

当前在 [`SessionManager.ensure_running()`](pkgs/bay/app/managers/session/session.py:89) 中，是否需要重建 Session 的判断主要依赖 DB 中的 `session.observed_state` / `session.container_id`：

- `session.observed_state == RUNNING` 且 `session.is_ready == True` 时直接返回
- 不会在每次请求时核对容器真实状态（Docker 侧）

因此会出现以下状态差：

- 容器已退出（`docker kill`、OOM、进程崩溃）
- DB 里仍保留 `observed_state=RUNNING`、`endpoint`、`container_id`
- 后续 `/exec` 等请求在访问 runtime 时遇到连接拒绝/超时

### 1.2 我们要解决的具体问题

把“容器已死”的情况从**不透明的网络错误**，变成：

1. **可判定**：Bay 能在合理时机识别容器已死
2. **可恢复**：可选择自动重建 Session（或清晰返回可重试错误）
3. **可测试**：通过 Unit + Integration 覆盖 Crash/OOM/GC 并发等场景

### 1.3 非目标（本阶段不做）

- 不做多租户/owner 透传增强（见 [`pkgs/bay/app/drivers/docker/docker.py`](pkgs/bay/app/drivers/docker/docker.py:50) 的 label 预留）
- 不做可观测性体系接入（Prometheus / OTel）
- 不做跨实例一致性增强（多实例部署另议）

---

## 2. 方案对比与取舍

### 2.1 方案 A：保持现状（被动发现）

**策略**：不增加探活；当容器已死时，让 runtime 调用失败，客户端重试/stop。

优点：
- 零改动、零性能成本

缺点：
- 用户体验差：错误呈现为“调用 runtime 失败”，不是“容器已死”
- 恢复路径不一致：用户必须懂得如何 retry / stop / 等 GC

### 2.2 方案 B：主动探活（建议作为 Phase 1.5 实施）

**策略**：在 [`SessionManager.ensure_running()`](pkgs/bay/app/managers/session/session.py:89) 的关键路径中，在“信任 `observed_state=RUNNING`”之前，对容器进行一次轻量探测（Docker inspect via driver.status），识别 EXITED/NOT_FOUND 并触发重建。

优点：
- 用户体验稳定：Crash/OOM 后下一次请求能自动恢复（或快速返回 retryable）
- 逻辑集中：修复点聚焦在 Session 管理器

代价/风险：
- 每次 `ensure_running()` 增加一次 driver.status（通常为一次 Docker inspect）
- 需要定义清晰的“何时 probe / 何时跳过 probe”规则，避免重复探测

---

## 3. 目标行为（行为契约）

### 3.1 行为原则

- **正确性优先**：不允许 DB 显示 RUNNING 但容器实际已死还继续返回 ready
- **性能可控**：只在必要时 probe（例如 observed_state==RUNNING 且 container_id 存在）
- **失败可退化**：Docker 不可达时可退化为方案 A（返回原先路径的错误）

### 3.2 期望的 API 表现

以 `/v1/sandboxes/{id}/python/exec` 为例：

- **容器健康**：正常执行
- **容器已死**：
  - Bay 在请求内部**尝试自动恢复 1 次**（探活 → 清理 → 重建 → readiness）
  - 若恢复成功：返回 200（执行成功）
  - 若恢复失败：返回 **503 (retryable)**，提示稍后重试（并在内部 best-effort 做清理）

> 备注：503 的语义已有基础（如 [`SessionNotReadyError`](pkgs/bay/app/errors.py:1) 对应 starting/retry-after）。Phase 1.5 目标是让失败模式可预测，不引入新的错误类型。

---

## 4. 设计细节

### 4.1 Probe 触发条件

在 [`SessionManager.ensure_running()`](pkgs/bay/app/managers/session/session.py:89) 内新增：

仅当满足以下条件才 probe：

- `session.container_id is not None`
- 且 `session.observed_state == RUNNING`

原因：
- PENDING/STARTING/FAILED 这些状态本来就会走 create/start 流程，不需要 probe

### 4.2 Probe 行为

复用 [`SessionManager.refresh_status()`](pkgs/bay/app/managers/session/session.py:323)：

- 调用 `driver.status(container_id, runtime_port=...)`
- 映射到 session 状态
  - RUNNING -> RUNNING
  - EXITED/NOT_FOUND -> STOPPED（现实现如此）

### 4.3 发现“容器已死”的恢复策略

当 probe 后 `session.observed_state` 变为 STOPPED 或 `session.container_id` 被清空：

- 记录 warning log（包含 session_id/container_id）
- best-effort `driver.destroy(container_id)`（容器可能已不存在，忽略 404）
- 清理 DB 字段：
  - `session.container_id = None`
  - `session.endpoint = None`
  - `session.observed_state = PENDING`
- 然后继续走原有 create/start 路径

> 注意：这里不创建新 session record，而是在同一个 session record 上重建容器。这样与现有 `Sandbox.current_session_id` 绑定模型兼容。

### 4.4 并发与锁

`ensure_running` 的并发由 [`SandboxManager.ensure_running()`](pkgs/bay/app/managers/sandbox/sandbox.py:188) 内的 [`get_sandbox_lock()`](pkgs/bay/app/concurrency/locks.py:1) 保证：同一 sandbox 的请求串行。

因此：
- 方案 B 不需要额外的 session 级锁
- probe/重建不会导致同一 sandbox 出现多容器竞态（理论上）

---

## 5. 测试计划（可执行）

### 5.1 单元测试（新增）

新增至 [`pkgs/bay/tests/unit/managers/test_session_manager.py`](pkgs/bay/tests/unit/managers/test_session_manager.py:1)：

1. `ensure_running` 在 `observed_state=RUNNING` 时，会调用 `driver.status`
2. 当 `driver.status` 返回 EXITED/NOT_FOUND 时：
   - 清理 `container_id/endpoint`
   - 调用 `driver.destroy`（best-effort）
   - 再次走 create/start 流程并成功到 RUNNING
3. 当 `driver.status` 抛异常（Docker daemon 不可达）时：
   - 行为可定义为“退化为旧路径”（抛异常或走原始失败路径），但必须不 hang

> 需要对 [`tests.fakes.FakeDriver`](pkgs/bay/tests/fakes.py:1) 增加 minimal 能力：可配置 `status()` 返回值/抛错，记录调用次数。

### 5.2 集成测试（已有文件需改预期）

我们已经创建了 resilience 测试文件：

- [`pkgs/bay/tests/integration/resilience/test_container_crash.py`](pkgs/bay/tests/integration/resilience/test_container_crash.py:1)
- [`pkgs/bay/tests/integration/resilience/test_oom_killed.py`](pkgs/bay/tests/integration/resilience/test_oom_killed.py:1)
- [`pkgs/bay/tests/integration/resilience/test_gc_race_condition.py`](pkgs/bay/tests/integration/resilience/test_gc_race_condition.py:1)

当方案 B 实施后，`test_container_crash` 的核心断言应从“允许 500/503”调整为：

- kill 容器后下一次 exec 最终应返回 200（可能需要 retry/backoff）

`test_oom_killed` 的 profile 依赖（`oom-test`）建议在 [`pkgs/bay/tests/scripts/docker-host/config.yaml`](pkgs/bay/tests/scripts/docker-host/config.yaml:1) 增加专用 profile；否则自动 skip。

### 5.3 串行/并行分组建议

- Crash/OOM 测试：通常可并行（每个 sandbox 独立）
- GC Race 测试：建议串行（涉及手动触发 GC，可能影响全局）

串行控制由 [`pkgs/bay/tests/integration/conftest.py`](pkgs/bay/tests/integration/conftest.py:1) 的 `SERIAL_GROUPS` 管理。

---

## 6. 实施步骤（落地清单）

1. 在 [`SessionManager.ensure_running()`](pkgs/bay/app/managers/session/session.py:89) 增加 probe + 恢复逻辑
2. 扩展 [`FakeDriver`](pkgs/bay/tests/fakes.py:1) 以支持 status 模拟
3. 增加 3 个 unit tests（见 5.1）
4. 更新 integration tests 预期（Crash/OOM）
5. 更新 `SERIAL_GROUPS`（至少把 GC Race 放入 serial）

---

## 7. 风险与回滚

### 7.1 风险

- 性能：每次 ensure_running 多一次 docker inspect
- 行为改变：以前可能返回网络错误，现在可能触发重建导致请求更慢但成功

### 7.2 回滚

- probe 逻辑集中在 [`SessionManager.ensure_running()`](pkgs/bay/app/managers/session/session.py:89) 一处
- 删除新增代码即可回到方案 A 行为

---

## 8. 决策记录（已确认）

1. **容器已死处理策略**：请求内部尝试恢复 1 次；若仍失败则返回 503（retryable）。
2. **OOM 暴露策略**：本阶段不额外暴露 `oom_killed` 等原因字段；保持现有错误/日志即可。
