# E2E/Integration 测试重构计划（并行/串行 + 目录结构 + 去重）

> 状态：拟定
> 更新时间：2026-02-02

## 1. 目标与问题

**问题**
- 大量测试可并行，但缺少明确的分组与隔离策略。
- 部分测试必须串行（GC/TTL/全局资源），但目前只靠单个标记，容易被其他测试干扰。
- 目录结构过于扁平，难以区分“核心 API / 工作流 / 安全 / Shell / GC”等类别。

**目标**
- 明确可并行与必须串行的测试范围，并提供统一标记规则。
- 通过目录结构和 pytest 标记，让执行策略更可控。
- 减少 GC 时序影响，确保 GC 测试不被其他测试资源干扰。

---

## 2. 目录结构重构提案

现有目录：`pkgs/bay/tests/integration/`（平铺）

**建议拆分：**
```
pkgs/bay/tests/integration/
  core/
    test_auth.py
    test_minimal_path.py
    test_stop.py
    test_delete.py
    test_concurrent.py
    test_idempotency_e2e.py
    test_extend_ttl.py
  filesystem/
    test_filesystem.py
    test_file_transfer.py
  security/
    test_path_security.py
    test_capability_enforcement.py
  shell/
    test_shell_e2e.py
    test_shell_devops_workflow.py
  isolation/
    test_container_isolation.py
  workflows/
    test_interactive_workflow.py
    test_script_development.py
    test_project_init.py
    test_serverless_execution.py
    test_long_running_extend_ttl.py
    test_agent_coding_workflow.py
    test_mega_workflow.py
  gc/
    test_gc_e2e.py
    test_gc_workflow_scenario.py
  conftest.py
  __init__.py
  test_e2e_api.py
```

**说明**
- 仅移动文件，不改变测试逻辑（第一阶段）。
- `test_e2e_api.py` 继续作为聚合入口（可保留或后续移除）。

---

## 3. 非 Workflow 测试去重（不动场景测试）

### 3.1 去重原则（仅针对非 Workflow）

- **Workflow/场景测试不改动**。
- 非 Workflow（core/filesystem/shell/security/isolation/gc）优先去重“相同断言、相同路径、相同失败模式”。

### 3.2 重复点盘点（建议）

**A. Shell 基础能力重复**
- [`pkgs/bay/tests/integration/test_shell_e2e.py`](../../pkgs/bay/tests/integration/test_shell_e2e.py:1) 已覆盖 echo/pwd/ls/pipe/exit_code/cwd/环境变量。
- [`pkgs/bay/tests/integration/test_shell_devops_workflow.py`](../../pkgs/bay/tests/integration/test_shell_devops_workflow.py:1) 中部分用例再次覆盖相同基础命令行为。

**建议**：
- `shell/` 保留 `test_shell_e2e.py` 作为“基础 API 验证”。
- `test_shell_devops_workflow.py` 仅保留**工具链与真实场景**（git/node/npm/pnpm/curl/tar/find/awk/sed），删除重复的 echo/pwd/exit_code/pipe 断言。

**B. Filesystem 基础能力重复**
- [`pkgs/bay/tests/integration/test_filesystem.py`](../../pkgs/bay/tests/integration/test_filesystem.py:1) 已覆盖读/写/列/删。
- [`pkgs/bay/tests/integration/test_file_transfer.py`](../../pkgs/bay/tests/integration/test_file_transfer.py:1) 已覆盖上传/下载。

**建议**：
- `filesystem/` 内只保留这两组用例，其他非 workflow 测试中不要重复验证文件读写/下载内容细节（改为“存在性检查”即可）。

**C. Stop/Resume 与 TTL 重复**
- [`pkgs/bay/tests/integration/test_stop.py`](../../pkgs/bay/tests/integration/test_stop.py:1) 已覆盖 stop 语义与幂等。
- [`pkgs/bay/tests/integration/test_extend_ttl.py`](../../pkgs/bay/tests/integration/test_extend_ttl.py:1) 已覆盖 extend_ttl 的成功、幂等、过期、无限 TTL 失败。
- 非 workflow 其他测试中出现类似 stop/ttl 断言可考虑删除。

**建议**：
- stop/extend_ttl 的**强断言**仅保留在上述两个文件；其他非 workflow 用例改为轻量状态检查即可。

---

## 4. 并行/串行策略

### 3.1 统一标记策略

- **默认并行**：所有 `integration` 测试默认可并行执行。
- **必须串行**：使用 `@pytest.mark.xdist_group("gc")`、`@pytest.mark.xdist_group("serial")` 等分组。

建议新增统一标记：
- `gc_serial_mark`：GC/TTL 依赖时序、会影响全局资源的测试
- `serial_mark`：需要全局独占资源但非 GC 的测试（若未来出现）

### 3.2 GC/TTL 串行范围（建议）

必须串行（独占运行，避免干扰 GC 时序）：
- `gc/` 目录所有测试：
  - `test_gc_e2e.py`
  - `test_gc_workflow_scenario.py`
- 与 TTL 强时序依赖的测试：
  - `test_extend_ttl.py::test_extend_ttl_rejects_expired`
  - `test_long_running_extend_ttl.py` 中“过期后拒绝延长”的相关用例

### 3.3 并行测试范围（建议）

并行执行可显著缩短运行时间：
- `core/`、`filesystem/`、`security/`、`shell/`、`isolation/`、`workflows/`（除上面串行用例）

---

## 4. 执行策略（pytest-xdist）

推荐默认并行执行：
```
pytest pkgs/bay/tests/integration -n auto --dist loadgroup
```

- `loadgroup` 允许同一 `xdist_group` 在同 worker 内串行。
- GC/TTL 相关测试仍将被自动串行化，避免污染。

如需完全串行：
```
pytest pkgs/bay/tests/integration
```

---

## 5. GC 时序干扰隔离策略

**问题：** GC 测试容易被其他测试创建的 sandbox/volume/container 干扰。

**建议：**
1. **GC 测试专属分组**：所有 GC 测试必须 `xdist_group("gc")`。
2. **测试配置隔离**：GC 测试使用特定配置文件（例如 `tests/scripts/docker-host/config.yaml` 中独立 instance_id）。
3. **显式触发 GC**：始终通过 Admin API 触发 GC，避免自动 GC 与测试时序耦合。

---

## 6. 迁移步骤（最小破坏）

1. **目录重组**：移动测试文件到新目录结构。
2. **更新 import/聚合入口**：修正 `test_e2e_api.py` 的 re-export 路径。
3. **统一标记**：在 GC/TTL 串行测试中补全 `gc_serial_mark`。
4. **非 workflow 去重**：按 3.x 建议删除重复断言。
5. **pytest 运行说明**：在 README 或测试脚本中加入并行运行指引。

---

## 7. 风险与回退

- **风险**：路径移动导致 import 失效或聚合入口引用错误。
- **回退**：保留旧的 `test_e2e_api.py` 作为兼容入口，必要时恢复平铺结构。

---

## 8. 后续可选优化

- 将 GC 测试单独划分测试套件（如 `-m gc`）
- 在 CI 中分拆 job：并行 job + GC 串行 job
- 将 workflow 类测试按场景拆分为更细粒度模块
- 为非 workflow 测试设定覆盖范围清单，避免重复回流

---

## 9. 需要决策的问题

- 是否为 GC 测试提供独立的 Bay 实例配置/端口（完全隔离）？
- 是否保留 `test_e2e_api.py` 作为入口，或在重构后移除？

---

## 10. 建议的下一步

- 确认目录重组方案
- 确认 GC 串行范围
- 落地迁移（先移动，再修复 import/标记）
