# Bay 抽象实体概念

本文档梳理了 Bay 系统中的核心抽象实体概念，包括数据库模型、配置实体、运行时接口以及服务层抽象。

## 1. 核心领域实体 (Domain Entities)

这些实体直接映射到数据库表，是系统的核心数据模型。

### 1.1 Sandbox (沙箱)
- **定义**: Bay 唯一对外暴露的计算资源单元。它是客户端持有的稳定引用，聚合了计算 (Session) 和存储 (Cargo)。
- **特点**:
    - **稳定性**: 拥有稳定的 ID，客户端对其生命周期有完全控制权。
    - **聚合性**: 关联 Profile（规格）、Cargo（存储）和 Session（运行时）。
    - **透明性**: 底层的 Session 可以被系统自动回收和重建，对客户端透明。
- **状态**: `IDLE`, `STARTING`, `READY`, `FAILED`, `EXPIRED`, `DELETED`。状态是根据当前 Session 的状态实时计算得出的。
- **关键属性**: `id`, `owner`, `profile_id`, `cargo_id`, `current_session_id`, `expires_at` (TTL)。

### 1.2 Session (会话)
- **定义**: 代表一组正在运行的容器实例 (Container Group)。
- **特点**:
    - **临时性**: 是短暂的运行时资源，系统可根据策略（如空闲超时）随时回收。
    - **私有性**: 不直接暴露给外部 API，仅作为 Sandbox 的内部实现细节。
    - **多容器支持**: Phase 1 对应单个容器，Phase 2 支持多容器协作。
- **状态**: `PENDING`, `STARTING`, `RUNNING`, `DEGRADED`, `STOPPING`, `STOPPED`, `FAILED`。采用“期望状态” (Desired) vs “观察状态” (Observed) 的管理模式。
- **关键属性**: `id`, `sandbox_id`, `container_id` (主容器), `containers` (多容器状态), `endpoint`。

### 1.3 Cargo (货物/工作区)
- **定义**: 持久化的数据卷，用于在 Session 之间共享和持久化数据。
- **类型**:
    - **Managed (托管型)**: 随 Sandbox 创建，生命周期绑定 Sandbox，支持级联删除。
    - **External (外部型)**: 独立创建，生命周期独立，可被多个 Sandbox 挂载。
- **关键属性**: `id`, `owner`, `backend` (docker_volume/k8s_pvc), `mount_path` (固定为 `/workspace`).

### 1.4 Skill Learning Entities (技能学习实体)
用于支持 Agent 技能学习闭环的实体：
- **ExecutionHistory**: 代码或命令执行的原子记录，包含输入、输出、状态和耗时，作为“证据”。
- **SkillCandidate**: 提议的技能候选，包含代码/指令和关联的源 ExecutionHistory，经历 `DRAFT` -> `EVALUATING` -> `PROMOTED` 等状态。
- **SkillEvaluation**: 对 Candidate 的评估记录，包含评分和通过状态。
- **SkillRelease**: 晋升后的正式技能版本，支持版本控制、灰度 (`CANARY`/`STABLE`) 和回滚。

### 1.5 IdempotencyKey (幂等键)
- **定义**: 用于保证非安全操作（如创建沙箱）幂等性的记录。
- **机制**: 记录请求指纹和响应快照，在 TTL 内对相同请求返回缓存响应。

---

## 2. 配置实体 (Configuration Entities)

这些实体通常定义在配置文件或环境变量中，通过 Pydantic 模型加载。

### 2.1 ProfileConfig (配置档案)
- **定义**: 定义 Sandbox 的运行时规格模板。
- **内容**:
    - **镜像定义**: 包含主容器和辅助容器的镜像信息。
    - **资源限制**: CPU、内存限制。
    - **能力声明**: 该 Profile 支持的能力集合 (e.g., `python`, `shell`, `browser`)。
    - **超时策略**: 空闲回收时间。

### 2.2 ContainerSpec (容器规格)
- **定义**: Profile 中对单个容器的详细定义。
- **内容**: `name`, `image`, `runtime_type` (ship/gull), `env`, `capabilities` 等。

---

## 3. 运行时抽象 (Runtime Abstractions)

用于屏蔽底层基础设施和运行时差异的接口层。

### 3.1 Driver (驱动)
- **职责**: **容器生命周期管理**。负责“怎么把容器跑起来”。
- **范围**: create, start, stop, destroy 容器；创建/删除 volume。
- **实现**: `DockerDriver` (单机), `K8sDriver` (集群)。
- **Phase 2**: 增加了多容器编排接口 (`create_multi`, `create_session_network`)。

### 3.2 BaseAdapter (适配器)
- **职责**: **运行时通信协议适配**。负责“怎么跟容器里的程序说话”。
- **范围**: 将统一的 Capability 调用（如 `exec_python`）转换为特定运行时的 API 调用。
- **实现**:
    - `ShipAdapter`: 对接 Ship 运行时，提供 python/shell/fs 能力。
    - `GullAdapter`: 对接 Gull 运行时，提供 browser 能力。

### 3.3 CapabilityRouter (能力路由器)
- **职责**: **请求分发**。
- **逻辑**: 接收上层业务请求 -> 确保 Session 运行 -> 根据 Capability 查找对应的 Container -> 获取 Adapter -> 执行请求。
- **多容器路由**: 在 Phase 2 中，能够根据请求的能力类型（如 `browser`）自动路由到提供该能力的辅助容器。

### 3.4 AdapterPool (适配器池)
- **职责**: 维护 Adapter 实例的缓存池。
- **目的**: 复用 Adapter 及其内部的元数据缓存 (`/meta`)，减少对运行时的探测开销。

---

## 4. 服务层抽象 (Service Layer)

### 4.1 Managers (管理器)
- **SandboxManager**: 编排 Sandbox 生命周期，处理 TTL、锁、状态计算。
- **SessionManager**: 编排 Session 生命周期，核心是 `ensure_running` 的幂等启动逻辑和多容器协调。
- **CargoManager**: 管理存储卷的生命周期和引用计数保护。

### 4.2 GC (垃圾回收)
- **GCTask**: 具体的回收逻辑单元（如回收空闲 Session、过期 Sandbox）。
- **GCCoordinator**: 分布式协调器，确保多实例部署下 GC 任务不重复执行。

---

## 5. 实体关系图

```mermaid
graph TD
    Sandbox[Sandbox (Stable ID)]
    Session[Session (Ephemeral Container Group)]
    Cargo[Cargo (Persistent Volume)]
    Profile[ProfileConfig]

    Sandbox -->|聚合| Cargo
    Sandbox -->|聚合| Session
    Sandbox -->|基于| Profile

    Session -->|包含 1..N| Container[Container (Runtime)]
    Session -->|使用| Adapter[RuntimeAdapter]

    Adapter -->|通信| Container

    subgraph "Infrastructure Layer"
        Driver[Driver (Docker/K8s)]
    end

    Session -.->|由...管理| Driver
    Cargo -.->|由...管理| Driver
```
