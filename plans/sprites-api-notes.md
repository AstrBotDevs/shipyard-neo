# 参考 Sprites API 设计：对 Bay/Ship 的可借鉴点（笔记）

> 你贴的 Sprites 文档主要覆盖：
> - 资源管理（create/list/get/update/destroy）
> - 运行时状态（cold/warm/running）
> - 远程执行（WS exec + session 持久化 + attach）
> - 端口代理/网络策略/快照（checkpoints）
>
> 这里整理“可以参考的设计点”和“我们不该照搬的点”，并映射到当前文档：
> - 架构：[plans/bay-design.md](plans/bay-design.md:1)
> - API：[plans/bay-api.md](plans/bay-api.md:1)

## 1. 可以直接参考的设计点

### 1.1 资源状态机对外化（cold/warm/running）

Sprites 用 `cold | warm | running` 让调用方理解启动成本与即时可用性。

映射到 Bay：
- 我们已有聚合态建议（idle/starting/ready/failed/expired）（见[plans/bay-api.md](plans/bay-api.md:122)）。
- 可借鉴：
  - 把 `idle` 命名为 `cold`（更贴近外界心智），把 `starting` 视为 `warming`，把 `ready` 视为 `running`。
  - 但不要为了对齐术语牺牲语义：我们的 `stop` 语义（只回收算力）与 `ttl/idle_timeout` 更细。

结论：参考“状态可见”与“状态名更贴近用户心智”，不必一模一样。

### 1.2 WS exec 的会话语义（session 持久化 + attach）

Sprites 的 exec API 最大价值：
- 断线不杀进程
- 重新 attach 能拿 scrollback
- 统一 stdin/stdout/stderr 的复用协议

映射到 Bay/Ship：
- Ship 已有 `GET /shell/processes`（见[pkgs/ship/app/components/shell.py](pkgs/ship/app/components/shell.py:60)）与 WebSocket terminal（见[pkgs/ship/app/components/term.py](pkgs/ship/app/components/term.py:101)）。
- Bay 在 Phase 1 可保持 REST（一次性 exec）为主，Phase 2 才引入 WebSocket 终端代理（见[plans/bay-design.md](plans/bay-design.md:303)）。
- 可借鉴：
  - 为 shell exec 增加 `session_id` 与 attach 语义（后续做）
  - 用统一事件流（SSE/WS/NDJSON）输出长任务日志

结论：设计理念可参考，落地时机放 Phase 2/3。

### 1.3 统一的分页 token（continuation_token）

Sprites 的 list 使用 continuation_token（更适合云服务规模）。

映射到 Bay：
- 我们在 v1 里已经选择 cursor 模式（见[plans/bay-api.md](plans/bay-api.md:59)）。
- 可以借鉴其字段命名与返回结构：`has_more` + `next_cursor`。

### 1.4 “默认认证方式”与 URL access settings

Sprites 把 URL 的访问方式（public/sprite auth）独立成 `url_settings`。

映射到 Bay：
- Bay 对外主要是 API（不是给每个 sandbox 分配公网 URL）。
- 但未来如果要暴露“可访问的 endpoint”（比如 Web IDE、dev server 端口），可以引入类似的 access policy：
  - public / auth / org-only
  - 与 NetworkPolicy/PortProxy 一起设计

结论：现在不做，但这是一个干净的扩展方向。

## 2. 不建议照搬的点（或需要重写）

### 2.1 Sprites 的“持久化计算环境”与我们的“短生命周期容器”不一致

Sprites 的核心卖点是环境状态长期保留（类似 VM/持久容器），而 Bay 的核心原则是 `1 Session : 1 Container`、计算易变（见[plans/bay-design.md](plans/bay-design.md:9)）。

我们能学：
- “对外稳定资源句柄”
我们不该学：
- 把计算实例变成永生（那会推高隔离/升级/回收复杂度）

### 2.2 Port proxy / network policy / checkpoints 都是大功能

这些功能会引入：
- 网络栈/代理/证书/域名/多租户隔离
- 存储快照/一致性/性能与成本

对于 Bay：
- 应放 Phase 2/3/4，且每个都值得单独的设计文档。

## 3. 对我们当前文档的“可操作修改建议”

### 3.1 在 Bay API 文档里补充字段命名对齐

在[plans/bay-api.md](plans/bay-api.md:59)分页响应建议加入：
- `has_more`（bool）
- `next_cursor`（string）

### 3.2 在 Bay Design 里增加一个“未来能力对标”小节（可选）

在[plans/bay-design.md](plans/bay-design.md:349)之后补充：
- exec sessions（attach/scrollback）
- port proxy
- network policy
- checkpoints

并标注 Phase。

## 4. 我们已经做对的部分（和 Sprites 很像）

- 对外稳定资源：sandbox_id（见[plans/bay-design.md](plans/bay-design.md:141)）
- stop vs delete 语义分离（见[plans/bay-api.md](plans/bay-api.md:254)）
- 运行时自描述：Ship `GET /meta`（见[pkgs/ship/app/main.py](pkgs/ship/app/main.py:62)）

这些是“云服务化编排层”绕不开的共性设计，借鉴是合理的。
