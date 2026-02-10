# Bay 错误码参考

> 源码: [`errors.py`](pkgs/bay/app/errors.py)
>
> 本文档是 [Bay API v1 参考文档](bay_api_v1.md) 的配套文档。

## 错误响应格式

所有业务错误继承自 [`BayError`](pkgs/bay/app/errors.py:12)，统一返回以下 JSON 格式：

```json
{
  "error": {
    "code": "error_code",
    "message": "Human-readable message",
    "request_id": "optional-request-id",
    "details": {}
  }
}
```

---

## 错误码一览

| 错误码 | HTTP 状态码 | 说明 | 源码 |
|--------|-------------|------|------|
| `not_found` | 404 | 资源不存在或不可见 | [`NotFoundError`](pkgs/bay/app/errors.py:41) |
| `unauthorized` | 401 | 需要认证 | [`UnauthorizedError`](pkgs/bay/app/errors.py:49) |
| `forbidden` | 403 | 权限不足 | [`ForbiddenError`](pkgs/bay/app/errors.py:57) |
| `validation_error` | 400 | 请求参数校验失败 | [`ValidationError`](pkgs/bay/app/errors.py:137) |
| `invalid_path` | 400 | 无效文件路径（绝对路径、路径穿越、null 字节等） | [`InvalidPathError`](pkgs/bay/app/errors.py:177) |
| `capability_not_supported` | 400 | Profile 不支持请求的能力 | [`CapabilityNotSupportedError`](pkgs/bay/app/errors.py:156) |
| `conflict` | 409 | 幂等键冲突或状态冲突 | [`ConflictError`](pkgs/bay/app/errors.py:113) |
| `sandbox_expired` | 409 | 沙箱已过期，不能延长 TTL | [`SandboxExpiredError`](pkgs/bay/app/errors.py:121) |
| `sandbox_ttl_infinite` | 409 | 沙箱 TTL 无限，无需延长 | [`SandboxTTLInfiniteError`](pkgs/bay/app/errors.py:129) |
| `file_not_found` | 404 | 沙箱工作区中文件不存在 | [`CargoFileNotFoundError`](pkgs/bay/app/errors.py:145) |
| `quota_exceeded` | 429 | 配额或速率限制超出 | [`QuotaExceededError`](pkgs/bay/app/errors.py:65) |
| `session_not_ready` | 503 | 会话正在启动中 | [`SessionNotReadyError`](pkgs/bay/app/errors.py:73) |
| `ship_error` | 502 | Ship 运行时错误 | [`ShipError`](pkgs/bay/app/errors.py:105) |
| `timeout` | 504 | 操作超时 | [`RequestTimeoutError`](pkgs/bay/app/errors.py:94) |
| `internal_error` | 500 | 内部错误 | [`BayError`](pkgs/bay/app/errors.py:12) |

---

## 错误详情示例

### `capability_not_supported`

当请求的能力不在 Profile 支持列表中时触发。`details` 包含请求的能力名和可用能力列表。

```json
{
  "error": {
    "code": "capability_not_supported",
    "message": "Profile 'python-default' does not support capability: browser",
    "details": {
      "capability": "browser",
      "available": ["filesystem", "python", "shell"]
    }
  }
}
```

### `session_not_ready`

会话正在启动中（冷启动），客户端应在 `retry_after_ms` 后重试。

```json
{
  "error": {
    "code": "session_not_ready",
    "message": "Session is starting",
    "details": {
      "sandbox_id": "sbx_abc123",
      "retry_after_ms": 2000
    }
  }
}
```

### `sandbox_expired`

尝试延长已过期沙箱的 TTL 时触发。Bay 不支持"复活"过期沙箱。

```json
{
  "error": {
    "code": "sandbox_expired",
    "message": "sandbox is expired, cannot extend ttl"
  }
}
```

### `sandbox_ttl_infinite`

尝试延长无限 TTL（`expires_at=null`）沙箱的 TTL 时触发。

```json
{
  "error": {
    "code": "sandbox_ttl_infinite",
    "message": "sandbox ttl is infinite, cannot extend ttl"
  }
}
```

### `invalid_path`

文件路径校验失败时触发。以下情况会被拒绝：
- 空路径
- 绝对路径（以 `/` 开头）
- 路径穿越（`../` 逃逸工作区边界）
- 包含 null 字节

```json
{
  "error": {
    "code": "invalid_path",
    "message": "Invalid path: path traversal detected in 'path'",
    "details": {}
  }
}
```

### `conflict`（Cargo 删除冲突）

尝试删除仍被活跃沙箱引用的外部 Cargo 时触发。

```json
{
  "error": {
    "code": "conflict",
    "message": "Cargo is still referenced by active sandboxes",
    "details": {
      "active_sandbox_ids": ["sbx_001", "sbx_002"]
    }
  }
}
```

---

## 按 HTTP 状态码分类

### 4xx 客户端错误

| 状态码 | 错误码 | 典型场景 |
|--------|--------|----------|
| 400 | `validation_error` | 请求体字段类型错误、缺少必填字段 |
| 400 | `invalid_path` | 文件路径包含 `../` 或以 `/` 开头 |
| 400 | `capability_not_supported` | 向 `python-default` Profile 发送 `browser/exec` 请求 |
| 401 | `unauthorized` | 未携带 Bearer token 且未开启匿名模式 |
| 403 | `forbidden` | 权限不足（当前版本较少触发） |
| 404 | `not_found` | 沙箱/Cargo/执行记录不存在 |
| 404 | `file_not_found` | 工作区中文件不存在 |
| 409 | `conflict` | 幂等键请求体不匹配 |
| 409 | `sandbox_expired` | 延长已过期沙箱的 TTL |
| 409 | `sandbox_ttl_infinite` | 延长无限 TTL 沙箱的 TTL |
| 429 | `quota_exceeded` | 超出并发限制或配额 |

### 5xx 服务端错误

| 状态码 | 错误码 | 典型场景 |
|--------|--------|----------|
| 500 | `internal_error` | 未预期的内部异常 |
| 502 | `ship_error` | Ship 运行时通信失败或返回错误 |
| 503 | `session_not_ready` | 容器冷启动中，应重试 |
| 504 | `timeout` | 代码执行或命令执行超时 |
