# Browser Automation Deep Dive (Gull Passthrough)

This document is the **detailed** browser automation guide for Shipyard Neo.

Use it only when:

- a browser workflow is **complex** (multi-step login, conditional branching, file upload/download, state reuse), or
- automation is **flaky / repeatedly failing**, and the root cause needs systematic diagnosis.

For everyday automation, start with the short guide in `references/browser.md` and follow the stable cadence.

---

## 0. Mental Model (Most Important)

Browser automation runs in the **Gull** runtime, which **passthrough-executes** the `agent-browser` CLI.

### 0.1 What you send as `cmd`

Treat `cmd` as **one command line** that is split into argv and executed **directly**. It is **not a shell script**.

Consequences:

- Shell operators do **not** work: redirection (`>`), pipes (`|`), chaining (`&&`, `;`).
- Shell expansions do **not** work: `$VAR`, `~`, globbing (`*.png`), heredocs.
- If an argument contains spaces, **wrap it in quotes** so it stays a single argument:
  - ✅ `fill @e1 "hello world"`
  - ❌ `fill @e1 hello world`

### 0.2 Prefix and injected parameters

In Shipyard Neo’s standard integration, the runtime **injects**:

- `--session` (bound to the Sandbox ID)
- `--profile /workspace/.browser/profile/` (browser state persistence on the shared Cargo Volume)

Therefore:

- Do **not** prefix commands with `agent-browser`.
- Do **not** include `--session` or `--profile` in `cmd`.

If you include them anyway, you risk confusing parsing or breaking the isolation/persistence contract.

---

## 1. Quick Start

A stable baseline for most tasks is:

1. **Navigate**: `open <url>`
2. **Wait**: `wait --load networkidle`
3. **Snapshot**: `snapshot -i`
4. **Interact**: `click` / `fill` / `select` / `press` ... using refs
5. **Re-snapshot** after any meaningful DOM change

Example command sequence:

```text
open https://example.com/form
wait --load networkidle
snapshot -i
fill @e1 "user@example.com"
fill @e2 "password123"
click @e3
wait --load networkidle
snapshot -i
```

---

## 2. Writing Rules (Non-negotiable for reliability)

### 2.1 `cmd` is not a shell

Do not send these patterns:

- `get text body > page.txt`
- `snapshot -i | grep ...`
- `open https://a.com && snapshot -i`

Correct “save text to file” workflow:

1. Run a `get ...` command and capture its returned stdout.
2. Write it into a workspace file using filesystem write.

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

## 4. Command Reference (as `cmd` strings)

All commands below are intended to be passed as `cmd` **without** an `agent-browser` prefix.

### 4.1 Navigation

```text
open <url>      # Navigate to URL (aliases: goto, navigate)
back            # Back
forward         # Forward
reload          # Reload
close           # Close browser
connect 9222    # Connect via CDP port (requires runtime support)
```

### 4.2 Snapshot (page analysis)

```text
snapshot              # Full accessibility tree
snapshot -i           # Interactive elements only (recommended)
snapshot -c           # Compact output
snapshot -d 3         # Limit depth
snapshot -s "#main"   # Scope to CSS selector
snapshot -i -C        # Include cursor-interactive elements
snapshot @e9          # Show subtree under a container ref (when supported)
```

### 4.3 Interaction (use refs from snapshot)

```text
click @e1
dblclick @e1
focus @e1
fill @e2 "text"        # Clear then type
type @e2 "text"        # Type without clearing
press Enter
press Control+a
keydown Shift
keyup Shift
hover @e1
check @e1
uncheck @e1
select @e1 "value"
select @e1 "a" "b"      # Multi-select
scroll down 500
scrollintoview @e1       # aliases: scrollinto
drag @e1 @e2
upload @e1 /workspace/file.pdf
```

### 4.4 Get information

```text
get text @e1
get html @e1
get value @e1
get attr @e1 href
get title
get url
get count ".item"
get box @e1
get styles @e1
```

### 4.5 State checks

```text
is visible @e1
is enabled @e1
is checked @e1
```

### 4.6 Screenshot / PDF (artifacts)

Prefer writing artifacts into the shared Cargo workspace so other containers (and download endpoints) can access them:

```text
screenshot
screenshot /workspace/a.png
screenshot --full /workspace/full.png
pdf /workspace/page.pdf
```

### 4.7 Wait

```text
wait @e1
wait 2000
wait --text "Success"       # or -t
wait --url "**/dashboard"   # or -u
wait --load networkidle     # or -l
wait --fn "window.ready"    # or -f
```

### 4.8 Tabs / windows / frames / dialogs

```text
tab
tab new [url]
tab 2
tab close
window new
frame "#iframe"
frame main
dialog accept [text]
dialog dismiss
```

### 4.9 Storage & network

```text
cookies
cookies set name value
cookies clear
storage local
storage local key
storage local set k v
storage local clear

network route <url>
network route <url> --abort
network route <url> --body '{}'
network unroute [url]
network requests
network requests --filter api
```

---

## 5. Snapshot Refs (`@e1`, `@e2`, ...) and their lifecycle

Refs are compact element handles assigned by snapshot output. They reduce token cost and make interactions robust.

### 5.1 Conceptual snapshot output

```text
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

### 5.2 Lifecycle rule (must follow)

**After a page change, refs become invalid.**

```text
snapshot -i
click @e1            # navigation / DOM rebuild
snapshot -i          # MUST re-snapshot
click @e1            # use new page’s @e1
```

Operations that often invalidate refs:

- Clicking a link/button that navigates
- Submitting a form
- Opening modals/dropdowns (significant DOM changes)

### 5.3 Troubleshooting refs

- `Ref not found`: re-run `snapshot -i` and use the new refs.
- Element missing in snapshot: `scroll`/`wait` then snapshot again.
- Too many elements: scope using `snapshot -s "#main"` or `snapshot @eX`.

---

## 6. Sessions and persistence (auto-managed)

### 6.1 Session isolation

In native agent-browser, `--session <name>` isolates browser contexts.

In Shipyard Neo’s typical usage:

- Each sandbox has a single bound session (derived from Sandbox ID).
- To run multiple independent sessions in parallel, create multiple sandboxes.

### 6.2 Profile persistence

Browser state is persisted under:

- `/workspace/.browser/profile/`

Persisted data includes:

- Cookies
- localStorage / sessionStorage
- IndexedDB
- Cache

This enables “login once, reuse later” within the same sandbox/cargo lifecycle.

### 6.3 Optional explicit export/import

If you need explicit migration/debugging, write state files to `/workspace`:

```text
state save /workspace/auth-state.json
state load /workspace/auth-state.json
```

Security note: state files may contain sensitive tokens. Do not commit or leak.

---

## 7. Semantic locators (`find`)

Use `find` when refs are unstable or you want semantic targeting:

```text
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

## 8. JavaScript (`eval`)

Basic:

```text
eval document.title
eval 2+2
```

For complex scripts:

- Do not rely on shell-style stdin piping.
- Prefer passing code as a single argument if the tool supports it (e.g., base64 variants), otherwise simplify the expression.

---

## 9. File artifacts (screenshots/PDF/download/upload)

### 9.1 Artifacts to shared workspace

Write outputs to `/workspace` so they are accessible cross-container and retrievable:

```text
screenshot /workspace/screenshots/page.png
screenshot --full /workspace/screenshots/full.png
pdf /workspace/reports/page.pdf
```

### 9.2 Uploading files

`upload` needs a path that is visible inside the Gull container. Recommended workflow:

1. Upload the file into the sandbox workspace (so it exists under `/workspace/...`).
2. Run:

```text
upload @e1 /workspace/input/file.pdf
```

### 9.3 Saving text output (correct way)

Because `cmd` is not a shell, redirection does not work.

Correct workflow:

1. Run `get text body` (or other `get ...`) and capture stdout.
2. Write the captured text via filesystem write to a file like `page-text.txt`.

---

## 10. Advanced workflows

### 10.1 Authentication (login reuse)

Typical login:

```text
open https://app.example.com/login
wait --load networkidle
snapshot -i
fill @e1 "user@example.com"
fill @e2 "password123"
click @e3
wait --url "**/dashboard"
```

With profile persistence, later opening a dashboard URL may work without re-login (site-dependent).

### 10.2 Video recording

If the runtime supports recording commands, store the recording under `/workspace`:

```text
record start /workspace/recordings/demo.webm
open https://example.com
wait --load networkidle
snapshot -i
record stop
```

### 10.3 Proxy

Two patterns:

1. Container-level env vars (stable): `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`.
2. Command-level flag (if supported): place at the start:

```text
--proxy http://proxy.example.com:8080 open https://httpbin.org/ip
get text body
```

---

## 11. Deterministic templates (batch-style sequences)

These are templates for sequences that do **not** need intermediate reasoning. If you need branching decisions, run commands step-by-step and re-snapshot between interactions.

### 11.1 Form automation template

```text
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

### 11.2 Authenticated session reuse template

```text
open https://app.example.com/login
wait --load networkidle
snapshot -i
# fill @e1 "<username>"
# fill @e2 "<password>"
# click @e3
# wait --url "**/dashboard"
snapshot -i
screenshot /workspace/login-ok.png
```

Never store real credentials in the repo.

### 11.3 Capture workflow template

```text
open https://example.com
wait --load networkidle
screenshot --full /workspace/page-full.png
snapshot -i
pdf /workspace/page.pdf
close
```

---

## 12. FAQ

### 12.1 Why no `agent-browser` prefix?

Because the browser runtime runs `agent-browser` internally and expects you to send only the **subcommand** portion.

### 12.2 Can `--session` / `--profile` be provided in `cmd`?

Do not do it in the standard integration:

- session/profile are injected and managed centrally
- manual overrides can break isolation and persistence

### 12.3 Why doesn’t `get text body > a.txt` work?

Because `cmd` is not a shell. No redirection is interpreted.

### 12.4 Why doesn’t `eval --stdin` work via passthrough?

The passthrough execution does not provide a shell-like stdin piping flow; prefer single-argument variants or simplify the JS.

---

## Appendix A: Direct CLI debugging (optional)

If you have a way to run `agent-browser` directly inside the runtime container (outside the normal passthrough path), the raw CLI form is:

```bash
agent-browser --session debug --profile /workspace/.browser/profile open https://example.com
agent-browser --session debug snapshot -i
agent-browser --session debug click @e1
```

This is optional and is not the standard Shipyard Neo control path.
