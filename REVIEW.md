cha'kan# Shipyard-Neo 代码审查清单

> **审查者视角**: Linus Torvalds 风格的代码质量审查
>
> **核心原则**:
> - "好品味"是让特殊情况消失、数据结构正确、代码简洁清晰
> - **轻量化优先**：拒绝引入不必要的重型依赖，用最简单的方案解决实际问题
> - "Theory loses. Every single time." — 解决真实问题，不做过度设计

---

## 总体评价

这个项目架构清晰，分层合理（Bay 编排层 + Ship 运行时）。数据模型设计体现了"计算与存储分离"的核心理念。项目已经很好地保持了轻量化——仅依赖 FastAPI + SQLite/SQLModel + Docker，没有引入 Redis/etcd 等分布式组件。

以下审查建议均遵循**轻量化原则**，优先使用现有工具和简单方案。

---

## 🔴 高优先级审查项

### 1. 并发控制与锁机制

**文件**: [`pkgs/bay/app/managers/sandbox/sandbox.py`](pkgs/bay/app/managers/sandbox/sandbox.py:37)

**问题**: 使用了全局字典 `_sandbox_locks` 做内存锁，存在以下风险：

```python
# 当前实现
_sandbox_locks: dict[str, asyncio.Lock] = {}
_sandbox_locks_lock = asyncio.Lock()
```

**审查要点**:
- [ ] 内存锁在多实例部署时完全失效，仅靠 `SELECT FOR UPDATE` 可能不够
- [ ] `_cleanup_sandbox_lock()` 可能在 sandbox 仍在使用时被调用（竞态条件）
- [ ] 锁字典会无限增长，没有清理过期锁的机制
- [ ] [`ensure_running()`](pkgs/bay/app/managers/sandbox/sandbox.py:211) 中的 `await self._db.rollback()` 是一个非常规的并发控制方式，需要验证其正确性

**轻量化建议**:
- 单实例部署：当前方案足够，只需添加锁过期清理
- 多实例部署：用数据库 `version` 字段做乐观锁（已有），无需引入 Redis

---

### 2. 路径安全校验（Bay 侧缺失）

**文件**: [`pkgs/ship/app/workspace.py`](pkgs/ship/app/workspace.py:26)

**当前状态**: Ship 侧已实现 `resolve_path()` 做路径穿越防护，但 Bay 侧未验证。

**审查要点**:
- [ ] Bay 的 Capability API 直接将路径透传给 Ship，未做预校验
- [ ] 如果恶意用户发送 `../../../etc/passwd`，依赖 Ship 拦截是否足够安全？
- [ ] Bay 应该在转发前做一层校验（防御纵深）

**文件需审查**:
- [`pkgs/bay/app/adapters/ship.py`](pkgs/bay/app/adapters/ship.py:203) - `read_file`, `write_file` 等方法
- [`pkgs/bay/app/router/capability/capability.py`](pkgs/bay/app/router/capability/capability.py)

---

### 3. 异常处理与资源泄露

**文件**: [`pkgs/bay/app/managers/session/session.py`](pkgs/bay/app/managers/session/session.py:128)

**问题**: 容器创建后启动失败，容器未清理：

```python
# create container
container_id = await self._driver.create(...)
session.container_id = container_id
await self._db.commit()

# start container - 如果这里失败
try:
    endpoint = await self._driver.start(...)
except Exception as e:
    session.observed_state = SessionStatus.FAILED
    await self._db.commit()
    raise  # 容器已创建但未清理！
```

**审查要点**:
- [ ] 容器创建成功但启动失败时，容器会变成孤儿
- [ ] 需要在 `ensure_running` 失败路径中清理容器
- [ ] 考虑使用 `try/finally` 或事务性创建模式

---

### 4. 时间相关的竞态条件

**文件**: [`pkgs/bay/app/models/sandbox.py`](pkgs/bay/app/models/sandbox.py:77)

**问题**: `is_expired` 属性每次调用都重新计算 `datetime.utcnow()`：

```python
@property
def is_expired(self) -> bool:
    if self.expires_at is None:
        return False
    return datetime.utcnow() > self.expires_at  # 每次调用时间不同
```

**审查要点**:
- [ ] 同一个请求处理流程中多次检查 `is_expired` 可能得到不同结果
- [ ] [`compute_status()`](pkgs/bay/app/models/sandbox.py:83) 调用 `is_expired`，可能导致状态不一致
- [ ] 考虑在请求上下文中固定时间基准

---

### 5. httpx 客户端连接管理

**文件**: [`pkgs/bay/app/adapters/ship.py`](pkgs/bay/app/adapters/ship.py:64)

**问题**: 每次请求都创建新的 `AsyncClient`：

```python
async def _request(...):
    async with httpx.AsyncClient() as client:  # 每次请求都新建
        response = await client.request(...)
```

**审查要点**:
- [ ] 频繁创建/销毁连接，性能开销大
- [ ] 无法复用 HTTP/2 连接
- [ ] 应该使用连接池或单例客户端
- [ ] [`_wait_for_ready()`](pkgs/bay/app/managers/session/session.py:206) 同样的问题

**轻量化建议**: 在 `ShipAdapter` 类中持有一个 `httpx.AsyncClient` 实例，在 `__init__` 或首次使用时创建。这是零成本优化，不引入任何新依赖。

---

## 🟠 中优先级审查项

### 6. Shell 命令注入风险

**文件**: [`pkgs/ship/app/components/user_manager.py`](pkgs/ship/app/components/user_manager.py:240)

**代码**:
```python
sudo_args.extend([
    "bash",
    "-lc",
    f"cd {shlex.quote(str(working_dir))} && {command}",  # command 未转义
])
```

**审查要点**:
- [ ] `command` 直接拼接到 shell 命令中，虽然前面有 `shlex.quote(working_dir)`
- [ ] 用户传入的 `command` 可以包含任意 shell 命令（这可能是设计意图，但需要确认）
- [ ] 是否需要对危险命令做黑名单？
- [ ] cwd 参数已做校验，但 env 参数呢？

---

### 7. 后台进程内存泄露

**文件**: [`pkgs/ship/app/components/user_manager.py`](pkgs/ship/app/components/user_manager.py:24)

**问题**: 后台进程注册表永远增长：

```python
_background_processes: Dict[str, "BackgroundProcessEntry"] = {}
```

**审查要点**:
- [ ] 进程完成后，`_background_processes` 中的条目从不删除
- [ ] 长时间运行的 Ship 容器会累积大量僵尸条目
- [ ] `Process` 对象持有资源，可能导致内存泄露
- [ ] 需要添加清理机制（定时清理已完成进程）

**轻量化建议**: 
- 在 `get_background_processes()` 调用时顺便清理 `returncode is not None` 的条目
- 或设置最大条目数限制，FIFO 淘汰老条目
- 无需引入后台定时任务框架

---

### 8. 配置热加载与缓存

**文件**: [`pkgs/bay/app/config.py`](pkgs/bay/app/config.py:221)

**问题**: `@lru_cache` 导致配置无法动态更新：

```python
@lru_cache
def get_settings() -> Settings:
    ...
```

**审查要点**:
- [ ] 配置被永久缓存，无法热加载
- [ ] 测试中需要手动清除缓存：`get_settings.cache_clear()`
- [ ] 是否需要支持配置热更新？
- [ ] 考虑使用依赖注入而非全局单例

---

### 9. 数据库事务边界

**文件**: [`pkgs/bay/app/managers/sandbox/sandbox.py`](pkgs/bay/app/managers/sandbox/sandbox.py:381)

**问题**: `delete()` 方法中多次 commit，事务边界不清晰：

```python
async def delete(self, sandbox: Sandbox) -> None:
    # ... 销毁 sessions
    for session in sessions:
        await self._session_mgr.destroy(session)  # 可能有自己的 commit

    # 软删除 sandbox
    sandbox.deleted_at = datetime.utcnow()
    await self._db.commit()  # commit 1

    # 级联删除 workspace
    if workspace and workspace.managed:
        await self._workspace_mgr.delete(...)  # commit 2
```

**审查要点**:
- [ ] 如果中途失败，会留下部分删除的状态
- [ ] 应该使用单一事务包裹整个删除操作
- [ ] 或者改用最终一致性 + 重试机制

---

### 10. Profile 查找效率

**文件**: [`pkgs/bay/app/config.py`](pkgs/bay/app/config.py:186)

**问题**: 线性查找 Profile：

```python
def get_profile(self, profile_id: str) -> ProfileConfig | None:
    for profile in self.profiles:
        if profile.id == profile_id:
            return profile
    return None
```

**审查要点**:
- [ ] 每次请求都遍历整个 profiles 列表
- [ ] Profile 数量增多后性能下降
- [ ] 应该在初始化时构建 `dict[str, ProfileConfig]`

---

## 🟡 低优先级审查项

### 11. 错误类型命名冲突

**文件**: [`pkgs/bay/app/errors.py`](pkgs/bay/app/errors.py:142)

**问题**: 自定义 `FileNotFoundError` 与 Python 内置类型同名：

```python
class FileNotFoundError(BayError):  # 覆盖了 builtins.FileNotFoundError
    ...
```

**审查要点**:
- [ ] 可能导致 `except FileNotFoundError` 捕获错误的异常
- [ ] 建议重命名为 `WorkspaceFileNotFoundError` 或类似

---

### 12. 日志信息完整性

**文件**: [`pkgs/bay/app/drivers/docker/docker.py`](pkgs/bay/app/drivers/docker/docker.py:149)

**问题**: TODO 注释表明 owner 信息缺失：

```python
container_labels = {
    "bay.owner": "default",  # TODO: get from session/sandbox
    ...
}
```

**审查要点**:
- [ ] 容器标签中的 owner 被硬编码为 "default"
- [ ] 影响多租户隔离和资源追踪
- [ ] 需要从 session/sandbox 传递真实 owner

---

### 13. 硬编码的端口和超时

**文件**: 多处

**审查要点**:
- [ ] [`pkgs/bay/app/config.py:105`](pkgs/bay/app/config.py:105): `runtime_port: int | None = 8123`
- [ ] [`pkgs/bay/app/managers/session/session.py:176`](pkgs/bay/app/managers/session/session.py:176): `max_wait_seconds: float = 120.0`
- [ ] [`pkgs/bay/app/adapters/ship.py:43`](pkgs/bay/app/adapters/ship.py:43): `timeout: float = 30.0`
- [ ] 这些魔法数字应该集中到配置文件

---

### 14. 类型注解不一致

**文件**: [`pkgs/bay/app/api/v1/sandboxes.py`](pkgs/bay/app/api/v1/sandboxes.py:58)

**问题**: 函数参数类型注解使用了旧风格：

```python
def _sandbox_to_response(sandbox, current_session=None) -> SandboxResponse:
    # sandbox 和 current_session 没有类型注解
```

**审查要点**:
- [ ] 部分函数缺少完整的类型注解
- [ ] 混用 `str | None` 和 `Optional[str]`，应统一风格

---

### 15. 测试覆盖度

**文件**: `pkgs/bay/tests/`, `pkgs/ship/tests/`

**审查要点**:
- [ ] 单元测试是否覆盖了并发场景？
- [ ] 是否有针对资源泄露的测试？
- [ ] 是否有边界条件测试（如 TTL=0, 空命令等）？
- [ ] 集成测试是否模拟了网络分区、容器崩溃等异常场景？

---

## 📋 架构层面审查

### 16. GC 机制未实现

**状态**: TODO 中标记为 🔴 高优先级，但代码中尚未实现

**审查要点**:
- [ ] Idle Session 回收逻辑缺失
- [ ] 过期 Sandbox 清理逻辑缺失
- [ ] 孤儿容器检测缺失
- [ ] 需要后台任务调度器

**轻量化建议**:
- 使用 FastAPI 的 `BackgroundTasks` 或简单的 `asyncio.create_task` 定时循环
- 避免引入 Celery/APScheduler 等重型任务队列
- 单实例部署下，一个简单的 `while True: await asyncio.sleep(60)` 循环就够了

---

### 17. 可观测性缺失

**审查要点**:
- [ ] 无 Prometheus metrics 埋点
- [ ] 无分布式追踪 (OpenTelemetry)
- [ ] 日志缺少请求上下文（虽然有 request_id，但未贯穿）
- [ ] 无健康检查的详细状态（仅返回 `{"status": "healthy"}`）

**轻量化建议**:
- Metrics: 考虑 `prometheus-fastapi-instrumentator`（<100行接入）
- Tracing: 暂缓，等有真实排查需求再加
- 健康检查: 增加数据库连接检测、Docker 连接检测即可

---

### 18. 数据迁移策略

**审查要点**:
- [ ] 使用 SQLite 作为默认数据库，生产环境迁移路径不清晰
- [ ] 无 Alembic 迁移脚本（或未找到）
- [ ] Model 变更后的向后兼容性

**轻量化建议**:
- 保持 SQLite 作为默认选项，对于绝大多数单机部署场景足够
- 仅当明确需要多实例时再考虑 PostgreSQL
- 用 `alembic` 管理迁移，它是标准做法且足够轻量

---

## 🔧 语言选型讨论：Python vs Rust/Go

### 现状分析

当前项目全栈使用 Python (FastAPI)，这是一个**合理的初期选择**：
- 开发速度快，生态丰富
- 团队熟悉度高（假设）
- 原型验证阶段足够

### 考虑引入 Rust/Go 的场景

| 组件 | 当前语言 | 是否值得重写 | 理由 |
|:---|:---|:---|:---|
| **Bay (编排层)** | Python | ⚠️ 可考虑 Go | 高并发 API 网关，Go 的 goroutine 模型更轻量 |
| **Ship (运行时)** | Python | ❌ 不建议 | 核心是 IPython 内核，必须用 Python |
| **Driver 层** | Python | 🟡 远期考虑 | 与 Docker/K8s API 交互，Go 有天然优势 |
| **路径校验/安全** | Python | ✅ 可考虑 Rust | 性能敏感 + 安全关键路径 |

### 🟢 推荐策略：渐进式混合架构

**Phase 1：保持现状**
- 当前 Python 代码能正常工作
- 重写的成本远大于收益
- 先把功能做完，再考虑优化

**Phase 2：识别热点路径** (如果遇到性能瓶颈)
- 用 profiler 找出真正的瓶颈
- 通常是 10% 的代码占 90% 的时间

**Phase 3：局部重写** (如果有真实需求)

可优先考虑用 Rust/Go 重写的部分：

1. **Bay 核心**（如果选择重写）
   ```
   Go 优势：
   - 编译为单一二进制，部署简单
   - goroutine 比 asyncio 更轻量
   - 内存占用远低于 Python
   - 启动时间 <100ms
   ```

2. **路径安全校验 FFI 模块**
   ```
   Rust 优势：
   - 内存安全，无 GC
   - 可编译为 Python 扩展 (PyO3)
   - 适合安全关键逻辑
   ```

### ❌ 不建议重写的部分

1. **Ship 运行时**
   - 核心是 IPython 内核（纯 Python）
   - 与 Python 生态深度绑定
   - 用其他语言反而增加复杂度

2. **SDK**
   - 目标用户是 Python/AI 开发者
   - 保持 Python 是正确选择

### 💡 Linus 的观点

```
"我是个该死的实用主义者。"

用什么语言不重要，重要的是：
1. 这是个真问题还是臆想的？—— 你现在有性能问题吗？
2. 解决方案的复杂度是否与问题匹配？—— 重写的成本 vs 收益
3. 团队能否维护？—— 引入新语言意味着新的学习曲线

如果 Python 性能真的成为瓶颈（而不是"理论上可能"），
再考虑用 Go 重写 Bay 编排层。
但 Ship 必须保持 Python——这是它的核心价值。
```

### 结论

| 决策点 | 建议 |
|:---|:---|
| 现阶段 | 保持全 Python，专注功能完成 |
| 遇到 CPU 瓶颈 | 用 Go 重写 Bay |
| 遇到内存瓶颈 | 优化现有 Python 代码 + 考虑 Rust FFI |
| Ship 运行时 | 永远保持 Python |

---

## ✅ 已做得较好的部分

1. **数据模型设计**: Sandbox/Session/Workspace 分离清晰，`desired_state` vs `observed_state` 是正确的模式
2. **幂等性支持**: Idempotency-Key 机制完整
3. **路径隔离**: Ship 侧的 `resolve_path()` 实现正确
4. **Profile 抽象**: 运行时规格枚举化，避免无限自定义
5. **Adapter 模式**: ShipAdapter 为未来多运行时扩展留出了接口
6. **轻量化架构**: 仅依赖 FastAPI + SQLite + Docker，无 Redis/etcd/消息队列，部署简单

---

## 💡 轻量化原则提醒

在解决上述问题时，务必遵循：

1. **"解决实际问题，不解决想象的问题"**
   - 单实例部署占 90% 场景，优先保证它能用
   - 多实例支持可以通过数据库乐观锁解决，无需 Redis

2. **"最简单的代码是最好的代码"**
   - 清理机制用简单的条件判断，不引入后台任务框架
   - 连接池复用是零成本优化，应该立刻做

3. **"不要过度抽象"**
   - 现有的 Driver/Adapter 抽象已经足够
   - 不需要为"未来可能"的场景添加更多层

---

## 审查优先级总结

| 优先级 | 项目数 | 关键问题 |
|:---:|:---:|:---|
| 🔴 高 | 5 | 并发锁、路径安全、资源泄露、时间竞态、连接管理 |
| 🟠 中 | 5 | 命令注入、内存泄露、配置缓存、事务边界、查找效率 |
| 🟡 低 | 5 | 命名冲突、日志完整性、硬编码、类型注解、测试覆盖 |
| 📋 架构 | 3 | GC机制、可观测性、数据迁移 |

---

> **下一步**: 按优先级逐项解决，每项完成后在此文档标记 ✅
>
> **注意**: 所有修复方案应优先选择不引入新依赖的实现方式
