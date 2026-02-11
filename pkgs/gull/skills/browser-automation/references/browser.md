# Browser Automation Deep Dive

This is the **detailed** browser automation reference for the Gull runtime.

Use it when:

- A browser workflow is **complex** (multi-step login, conditional branching, file upload/download, state reuse)
- Automation is **flaky / repeatedly failing** and the root cause needs systematic diagnosis

For everyday automation, the main [SKILL.md](../SKILL.md) covers the essential workflow and command reference.

---

## 1. Mental Model

Browser automation runs in the **Gull** container, which **passthrough-executes** the `agent-browser` CLI.

### 1.1 What you send as `cmd`

Treat `cmd` as **one command line** that is split into argv and executed **directly**. It is **not a shell script**.

Consequences:

- Shell operators do **not** work: redirection (`>`), pipes (`|`), chaining (`&&`, `;`)
- Shell expansions do **not** work: `$VAR`, `~`, globbing (`*.png`), heredocs
- If an argument contains spaces, **wrap it in quotes**:
  - ✅ `fill @e1 "hello world"`
  - ❌ `fill @e1 hello world`

### 1.2 Injected parameters

The runtime **injects**:

- `--session` (bound to the Sandbox ID)
- `--profile /workspace/.browser/profile/` (browser state persistence on Cargo Volume)

Therefore:

- Do **not** prefix commands with `agent-browser`
- Do **not** include `--session` or `--profile` in `cmd`

---

## 2. Writing Rules (Non-negotiable for reliability)

### 2.1 `cmd` is not a shell

Do not send these patterns:

- `get text body > page.txt`
- `snapshot -i | grep ...`
- `open https://a.com && snapshot -i`

Correct "save text to file" workflow:

1. Run a `get ...` command and capture its returned stdout
2. Write it into a workspace file using filesystem write

### 2.2 Quoting rules

Because `cmd` is split like a CLI string, you must quote arguments with spaces:

- ✅ `fill @e1 "hello world"`
- ✅ `wait --url "**/dashboard"`
- ❌ `fill @e1 hello world`

### 2.3 Global CLI flags (optional)

If you use global flags (e.g., `--json`, `--proxy`), put them at the **very beginning** of `cmd`:

- ✅ `--json snapshot -i`
- ✅ `--proxy http://host:port open https://example.com`

Avoid putting global flags after subcommands.

---

## 3. Core Cadence: Navigate → Snapshot → Interact → Re-snapshot

Most failures come from breaking the ref lifecycle.

Stable cadence:

1. `open <url>`
2. `wait --load networkidle` (or other wait)
3. `snapshot -i` to obtain refs `@e1`, `@e2`, ...
4. `click` / `fill` / `select` / `press` using refs
5. If the page changed, **run `snapshot -i` again**

---

## 4. Snapshot Refs and Their Lifecycle

Refs are compact element handles assigned by snapshot output.

### 4.1 Conceptual snapshot output

```
Page: Example Site - Home
URL: https://example.com

@e1 [header]
  @e2 [nav]
    @e3 [a] "Home"
    @e4 [a] "Products"
  @e6 [button] "Sign In"

@e7 [main]
  @e8 [h1] "Welcome"
  @e9 [form]
    @e10 [input type="email"] placeholder="Email"
    @e11 [input type="password"] placeholder="Password"
    @e12 [button type="submit"] "Log In"
```

### 4.2 Lifecycle rule (must follow)

**After a page change, refs become invalid.**

```
snapshot -i
click @e1            # navigation / DOM rebuild
snapshot -i          # MUST re-snapshot
click @e1            # use new page's @e1
```

Operations that often invalidate refs:

- Clicking a link/button that navigates
- Submitting a form
- Opening modals/dropdowns (significant DOM changes)

### 4.3 Troubleshooting refs

- `Ref not found`: re-run `snapshot -i` and use the new refs
- Element missing in snapshot: `scroll`/`wait` then snapshot again
- Too many elements: scope using `snapshot -s "#main"` or `snapshot @eX`

---

## 5. Sessions and Persistence

### 5.1 Session isolation

Each sandbox has a single bound session (derived from Sandbox ID). To run multiple independent sessions in parallel, create multiple sandboxes.

### 5.2 Profile persistence

Browser state is persisted under `/workspace/.browser/profile/`:

- Cookies
- localStorage / sessionStorage
- IndexedDB
- Cache

This enables "login once, reuse later" within the same sandbox/cargo lifecycle.

### 5.3 Explicit state export/import

For migration or debugging:

```
state save /workspace/auth-state.json
state load /workspace/auth-state.json
```

Security note: state files may contain sensitive tokens. Do not commit or leak.

---

## 6. Semantic Locators (`find`)

Use `find` when refs are unstable or you want semantic targeting:

```
find role button click --name "Submit"
find text "Sign In" click
find text "Sign In" click --exact
find label "Email" fill "user@test.com"
find placeholder "Search" type "query"
find testid "submit-btn" click
find first ".item" click
find last ".item" click
find nth 2 "a" hover
```

---

## 7. File Artifacts

### 7.1 Writing artifacts to shared workspace

Write outputs to `/workspace` so they are accessible cross-container and retrievable:

```
screenshot /workspace/screenshots/page.png
screenshot --full /workspace/screenshots/full.png
pdf /workspace/reports/page.pdf
```

### 7.2 Uploading files

`upload` needs a path visible inside the Gull container. Recommended workflow:

1. Upload the file into the sandbox workspace (so it exists under `/workspace/...`)
2. Run: `upload @e1 /workspace/input/file.pdf`

### 7.3 Saving text output

Because `cmd` is not a shell, redirection does not work.

Correct workflow:

1. Run `get text body` (or other `get ...`) and capture stdout
2. Write the captured text via filesystem write to a file

---

## 8. Advanced Workflows

### 8.1 Authentication (login reuse)

```
open https://app.example.com/login
wait --load networkidle
snapshot -i
fill @e1 "user@example.com"
fill @e2 "password123"
click @e3
wait --url "**/dashboard"
```

With profile persistence, later opening a dashboard URL may work without re-login (site-dependent).

### 8.2 Video recording

If the runtime supports recording:

```
record start /workspace/recordings/demo.webm
open https://example.com
wait --load networkidle
snapshot -i
record stop
```

### 8.3 Proxy

Two patterns:

1. Container-level env vars: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`
2. Command-level flag: place at the start of `cmd`:

```
--proxy http://proxy.example.com:8080 open https://httpbin.org/ip
get text body
```

---

## 9. Batch Execution Templates

These are templates for sequences that do **not** need intermediate reasoning. If you need branching decisions, run commands step-by-step and re-snapshot between interactions.

### 9.1 Form automation template

```
open https://example.com/form
wait --load networkidle
snapshot -i
# replace refs based on snapshot output
# fill @e1 "Test User"
# fill @e2 "test@example.com"
# click @e3
# wait --load networkidle
snapshot -i
screenshot /workspace/form-result.png
close
```

### 9.2 Capture workflow template

```
open https://example.com
wait --load networkidle
screenshot --full /workspace/page-full.png
snapshot -i
pdf /workspace/page.pdf
close
```

---

## 10. FAQ

### 10.1 Why no `agent-browser` prefix?

Because the Gull runtime runs `agent-browser` internally and expects you to send only the **subcommand** portion.

### 10.2 Can `--session` / `--profile` be provided in `cmd`?

Do not do it — session/profile are injected and managed centrally. Manual overrides can break isolation and persistence.

### 10.3 Why doesn't `get text body > a.txt` work?

Because `cmd` is not a shell. No redirection is interpreted.

### 10.4 Why doesn't `eval --stdin` work?

The passthrough execution does not provide a shell-like stdin piping flow; prefer single-argument variants or simplify the JS.

---

## Appendix: Direct CLI Debugging (Optional)

If you have a way to run `agent-browser` directly inside the Gull container (outside the normal passthrough path):

```bash
agent-browser --session debug --profile /workspace/.browser/profile open https://example.com
agent-browser --session debug snapshot -i
agent-browser --session debug click @e1
```

This is optional and not the standard control path.
