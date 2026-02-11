---
name: browser-automation
description: "Gull runtime usage guide for browser automation sandboxes. Covers the agent-browser CLI passthrough model, snapshot-based element referencing, single vs batch execution strategies, artifact handling, and browser state persistence. Use this when working inside a Gull container to understand command syntax, critical rules for reliable automation, and common patterns for navigation, form filling, data extraction, and screenshot capture."
---

# Gull Runtime: Browser Automation

Gull is a containerized browser automation runtime that wraps the `agent-browser` CLI with automatic session isolation and browser state persistence. It exposes a thin HTTP API (`/exec`, `/exec_batch`) for executing browser commands.

## Container Base Image

- **Base**: `python:3.11-slim-bookworm` with uv package manager
- **Browser Engine**: Chromium (via Playwright)
- **CLI**: `agent-browser` (globally installed via npm)
- **Node.js**: v20 LTS
- **Workspace**: `/workspace` (shared Cargo Volume with Ship container)

## Architecture

```
┌──────────────────────────────────────┐
│          Gull Container              │
│                                      │
│  ┌──────────────────────────────┐    │
│  │      FastAPI Wrapper          │    │
│  │  POST /exec                   │    │
│  │  POST /exec_batch             │    │
│  └──────────┬───────────────────┘    │
│             │ subprocess             │
│  ┌──────────┴───────────────────┐    │
│  │      agent-browser CLI        │    │
│  │  + Playwright Chromium        │    │
│  └──────────┬───────────────────┘    │
│             │                        │
│  ┌──────────┴───────────────────┐    │
│  │  /workspace (Cargo Volume)    │    │
│  │  ├── .browser/profile/        │    │
│  │  └── (shared with Ship)       │    │
│  └──────────────────────────────┘    │
└──────────────────────────────────────┘
```

## Execution Model (Critical)

Treat `cmd` as a **single command line** that is split into arguments and executed directly. It is **NOT a shell script**.

### What this means

- **No shell operators**: `>`, `|`, `&&`, `;`, subshells, heredocs — none of these work
- **No environment expansion**: `$VAR`, `~`, globbing (`*.png`) are not interpreted
- **Quote arguments with spaces**: `fill @e1 "hello world"` ✅ vs `fill @e1 hello world` ❌
- **Orchestrate control flow in the agent loop**, not inside `cmd`

## Critical Rules (Non-negotiable)

1. **Never include `agent-browser` prefix** — Gull auto-injects it. Sending `agent-browser open ...` becomes `agent-browser agent-browser open ...` and fails.
2. **Never use `execute_shell` for browser commands** — Ship container does not have agent-browser installed.
3. **Always re-snapshot after DOM changes** — Element refs (`@e1`, `@e2`) are invalidated by navigation, form submissions, and dynamic content loading.
4. **Never include `--session` or `--profile` in `cmd`** — These are automatically injected by Gull for isolation and persistence.

## Auto-injected Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `--session` | Sandbox ID | Browser instance isolation |
| `--profile` | `/workspace/.browser/profile/` | Persistent browser state (cookies, localStorage, IndexedDB, cache) |

## Core Workflow: Navigate → Snapshot → Interact → Re-snapshot

Every browser automation follows this cycle:

```
open https://example.com/form
snapshot -i
# Output: @e1 [input type="email"], @e2 [input type="password"], @e3 [button] "Submit"

fill @e1 "user@example.com"
fill @e2 "password123"
click @e3
wait --load networkidle
snapshot -i    # MUST re-snapshot — previous refs are now invalid
```

## Command Reference

### Navigation

```
open <url>                    Navigate to URL (aliases: goto, navigate)
back                          Go back
forward                       Go forward
reload                        Reload page
close                         Close browser
```

### Snapshot (Page Analysis)

```
snapshot -i                   Interactive elements with refs (recommended)
snapshot -i -C                Include cursor-interactive elements (divs with onclick, cursor:pointer)
snapshot -s "#selector"       Scope to CSS selector
snapshot -i --json            JSON output for structured parsing
snapshot @e9                  Show subtree under a container ref
```

### Interaction (use @refs from snapshot)

```
click @e1                     Click element
dblclick @e1                  Double-click element
fill @e2 "text"               Clear field and type text
type @e2 "text"               Type without clearing
select @e1 "option"           Select dropdown option
check @e1                     Check checkbox
uncheck @e1                   Uncheck checkbox
press Enter                   Press key
press Control+a               Key combination
hover @e1                     Hover over element
scroll down 500               Scroll page (direction + pixels)
scrollintoview @e1            Scroll element into view
drag @e1 @e2                  Drag from one element to another
upload @e1 /workspace/file.pdf  Upload file to input element
```

### Get Information

```
get text @e1                  Get element text
get text body                 Get all visible page text
get html @e1                  Get element HTML
get value @e1                 Get input value
get attr @e1 href             Get element attribute
get title                     Get page title
get url                       Get current URL
get count ".item"             Count matching elements
get box @e1                   Get element bounding box
get styles @e1                Get computed styles
```

### Wait

```
wait @e1                      Wait for element to appear
wait --load networkidle       Wait for network idle
wait --url "**/dashboard"     Wait for URL pattern
wait --text "Success"         Wait for text to appear
wait --fn "window.ready"      Wait for JS function to return truthy
wait 2000                     Wait milliseconds
```

### Capture (Artifacts)

Write artifacts to `/workspace` so they are accessible cross-container:

```
screenshot                           Screenshot to temp dir
screenshot /workspace/page.png       Screenshot to specific path
screenshot --full /workspace/full.png  Full-page screenshot
pdf /workspace/page.pdf              Save page as PDF
```

### Semantic Locators (Alternative to Refs)

When refs are unavailable or unstable:

```
find text "Sign In" click
find label "Email" fill "user@test.com"
find role button click --name "Submit"
find placeholder "Search" type "query"
find testid "submit-btn" click
find first ".item" click
find last ".item" click
find nth 2 "a" hover
```

### State Checks

```
is visible @e1
is enabled @e1
is checked @e1
```

### JavaScript Evaluation

```
eval document.title
eval 2+2
```

### Tabs / Windows / Frames / Dialogs

```
tab                           List tabs
tab new [url]                 Open new tab
tab 2                         Switch to tab
tab close                     Close current tab
window new                    Open new window
frame "#iframe"               Switch to iframe
frame main                    Switch back to main frame
dialog accept [text]          Accept dialog
dialog dismiss                Dismiss dialog
```

### Storage & Network

```
cookies                       List cookies
cookies set name value        Set cookie
cookies clear                 Clear all cookies
storage local                 List localStorage
storage local key             Get localStorage value
storage local set k v         Set localStorage value
storage local clear           Clear localStorage

network route <url>           Intercept requests
network route <url> --abort   Block requests
network route <url> --body '{}' Mock response body
network unroute [url]         Remove route
network requests              Show captured requests
network requests --filter api Filter by pattern
```

### Browser State Management

```
state save /workspace/auth-state.json     Export browser state
state load /workspace/auth-state.json     Import browser state
```

> **Security note**: State files may contain sensitive tokens. Do not commit or share them.

## Ref Lifecycle (Must Understand)

Refs (`@e1`, `@e2`, etc.) are **ephemeral**. They become invalid when:

- Clicking links or buttons that cause navigation
- Form submissions
- Dynamic content loading (dropdowns, modals, AJAX updates)
- Page refresh

**Always re-snapshot after any action that may change the DOM**:

```
snapshot -i
click @e1            # may navigate or change DOM
snapshot -i          # MUST re-snapshot
click @e1            # use the NEW page's @e1
```

## Persisting Text Output to Files

Because `cmd` is not a shell, `get text body > page.txt` will **not** work.

Correct workflow:

1. Run a `get ...` command and capture the returned stdout
2. Write the captured text into a file via filesystem tools (e.g., `write_file`)

## Single vs Batch Execution

| Scenario | Approach | Reason |
|----------|----------|--------|
| First visit to a page | Single: `open` → `snapshot -i` | Need to see page structure before interacting |
| Known form with stable refs | Batch: `fill` → `fill` → `click` | Deterministic, saves round trips |
| Login with unknown form structure | Single: multiple calls with snapshots | Need intermediate analysis |
| Page navigation + verification | Single: `click` → `snapshot -i` | Must re-snapshot after navigation |
| Scraping multiple items | Single: `snapshot` → `get text` per item | Need to analyze each result |
| Open page + wait + screenshot | Batch: `open` → `wait` → `screenshot` | Deterministic sequence |

## Browser State Persistence

Gull automatically persists browser state to `/workspace/.browser/profile/` on the shared Cargo Volume:

| Persisted Data | Details |
|----------------|---------|
| Cookies | Session and persistent cookies |
| localStorage | Key-value storage |
| IndexedDB | Structured storage |
| Service Workers | Background workers |
| Cache | HTTP cache |

This state:

- Survives container restarts within the same sandbox
- Is automatically cleaned up when the sandbox is deleted
- Enables "login once, reuse later" within the same sandbox lifecycle

## Common Patterns

### Form Submission

```
open https://example.com/signup
snapshot -i
# Analyze output to identify form fields
fill @e1 "Jane Doe"
fill @e2 "jane@example.com"
select @e3 "California"
check @e4
click @e5
wait --load networkidle
snapshot -i    # Verify submission result
```

### Authentication Flow

```
# Step 1: Navigate
open https://app.example.com/dashboard
snapshot -i

# Step 2: Analyze snapshot
#   → If "Welcome" visible → already logged in → proceed
#   → If "Sign In" form visible → fill credentials:

fill @e1 "user@example.com"
fill @e2 "password"
click @e3
wait --load networkidle
snapshot -i

# Step 3: Verify login result
#   → Success → continue task
#   → Failure → analyze reason and adapt
```

### Data Extraction

```
open https://example.com/products
snapshot -i
get text @e5                # Get specific element text
get text body               # Get all page text
snapshot -i --json           # JSON output for structured parsing
```

### Screenshot and Cross-Container Processing

```
# Gull: take screenshot (writes to shared volume)
screenshot /workspace/page.png

# Ship: process it via Python
# execute_python: from PIL import Image; img = Image.open('page.png')
```

### Deterministic Batch

```
# No intermediate reasoning needed — use batch
commands: [
    "open https://example.com",
    "wait --load networkidle",
    "screenshot --full /workspace/full.png",
    "snapshot -i"
]
```

## Error Reference

| Error | Cause | Solution |
|-------|-------|----------|
| `agent-browser agent-browser ...` | Included `agent-browser` prefix in cmd | Remove prefix |
| `Element not found: @e5` | Ref invalidated by DOM change | Re-snapshot with `snapshot -i` |
| `Command timed out after Ns` | Page/element load too slow | Increase `timeout` parameter |
| `agent-browser not found` | Ran browser command on Ship (wrong container) | Use `execute_browser`, not `execute_shell` |
| `Sandbox does not have browser capability` | Profile lacks browser support | Use `browser-python` profile |

## Deep-Dive Reference

For complex workflows, conditional branching, file uploads/downloads, debugging flaky automation, and advanced topics (tabs, frames, network interception, proxy, recording), see [references/browser.md](references/browser.md).
