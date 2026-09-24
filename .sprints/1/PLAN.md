# Sprint 1 — stealth-chrome-devtools-mcp fork (underlines)

Fork of [DevinoSolutions/stealth-chrome-devtools-mcp](https://github.com/DevinoSolutions/stealth-chrome-devtools-mcp)
at [github.com/underlines/stealth-chrome-devtools-mcp](https://github.com/underlines/stealth-chrome-devtools-mcp).
Not intended to merge upstream; tracks our own additions on top of it.

## Overview

Upstream ships a stealth (anti-detection) browser-automation MCP server:
one shared backend, real Chrome driven over CDP via `nodriver`, launch-arg
filtering to strip automation signatures. A bot-detection evaluation against
10 public detectors (`BOT_DETECTION_REPORT.md`, repo root) found the
fingerprint layer solid — `navigator.webdriver`, CDP artifacts, headless UA,
automation globals all came back clean — but confirmed the tool does **zero
behavioral humanization**: every click lands instantly on the exact
geometric center of its target, every keystroke is paced by one fixed
constant. That gap, plus wanting the tool deployable outside a local dev
machine and eventually watchable/steerable by a human, is what this sprint's
three workstreams address.

| Workstream | Status |
|---|---|
| 1. Dockerization | **Done** |
| 2. Humanization (click paths, typing cadence) | **Done** (first pass) |
| 3. Human-in-the-loop remote steering + "need help" tool | **Proposed / not started** |

---

## 1. Dockerization — done

`Dockerfile` + `docker-compose.yml` + `.dockerignore` at the repo root.

- Two-stage build: `uv sync --frozen` against the repo's own `uv.lock` in a
  builder stage, then a `python:3.11-slim-bookworm` runtime stage with
  Google Chrome Stable + its headless dependency set installed.
- Runs the backend's standalone **HTTP transport**
  (`stealth-chrome-devtools-mcp --transport http --host 0.0.0.0`), which is
  the mode built for this — no desktop, no stdio proxy, headless-only.
- State persists in two named Docker volumes so profiles/logins/registry
  survive `docker compose down`/`up`:
  - `stealth_mcp_state` → `~/.stealth-mcp` (registry, logs, locks)
  - `stealth_mcp_sessions` → `~/.stealth-mcp-browser-sessions` (profiles,
    clones, the shared `default` session and its seed)
- `shm_size: 2gb` (Chrome's default 64 MiB `/dev/shm` crashes it); the
  published port binds `127.0.0.1` only by default, since the backend is
  unauthenticated and drives logged-in profiles.
- One real bug found and fixed during verification: the image's Chrome wraps
  `/usr/bin/google-chrome` in a shim that drops the `--single-process` flag
  the server auto-adds inside containers — Chrome 154 exits immediately with
  that flag set (measured). Documented in the Dockerfile and README; delete
  the shim if a future Chrome release stops needing it.
- Verified end to end: built, ran, confirmed the MCP endpoint answers at
  `http://localhost:8000/mcp`, registered it with `claude mcp add --transport
  http`, and drove real spawns/navigations/clicks through it (see the bot
  detection report — all 10 tests ran against this container).

## 2. Humanization — done (first pass)

New module: `src/stealth_chrome_devtools_mcp/embedded/humanize.py`.
Toggle: `spawn_browser(humanize=True)` — **off by default**, instance-scoped
(set once at spawn, read by `click_element`/`type_text` for that instance's
whole lifetime, never a per-call override — one place a session's
humanization mode lives, so it cannot drift call to call).

### Why grounded in a recorded trace, not invented randomness

A `random.uniform()` jitter is itself a statistical signature — real human
timing is heavy-tailed and skewed (most keystrokes fast, occasional long
pause), not flat. `humanize.py`'s quantile tables (percentile → value,
piecewise-linear, sampled via inverse-CDF) are derived from a real recorded
session: a front-page visit on an online newspaper, scrolling, clicking into
an article, reading, and posting a comment (~1700 events, ~65 seconds). The
recorder is privacy-respecting by construction — its own trace carries
`"textCaptured": false` and `"keyValuesCaptured": false`: no page text and no
actual key VALUES were ever recorded, only event timing, pointer
coordinates, and a coarse key class (`printable`/`space`/`modifier`/
`delete`). That is also `humanize.py`'s own discipline, matching this repo's
existing PII rules elsewhere (F-869/F-873/F-876/F-877): nothing here ever
needs to know, or carries, what was actually typed or clicked.

Extracted distributions (percentiles in ms unless noted):

| Signal | n | p25 | median | p75 | p95 |
|---|---|---|---|---|---|
| keystroke interval, printable | 69 | 94 | 111 | 188 | 1093 |
| keystroke interval, space | 10 | 95 | 218 | 876 | 1844 |
| mouse micro-step dt | 632 | 15 | 16 | 17 | 46 |
| mouse micro-step distance (px) | 632 | 1.4 | 3.6 | 9.2 | 33 |
| click dwell (pointerdown→up) | 3 | — | 77 | — | — |

### What it does

- **`click_element`**: when the instance is humanized and the target has a
  real click point, `dom_handler._humanized_click` moves the mouse along a
  quadratic-Bezier path (`humanize.natural_mouse_path`) from the pointer's
  last known position (tracked per-tab), with each intermediate
  `mouseMoved` paced by the recorded micro-step timing, then presses, holds
  for a sampled dwell, and releases — instead of `Element.mouse_click()`'s
  instant press/release at the center. Falls back to the ordinary path for
  an unrendered target (nothing to move toward).
- **`type_text`**: when humanized, `text_entry.type_characters` draws each
  inter-keystroke delay from the recorded quantile table (by class) instead
  of the caller's fixed `delay_ms`.
- Verified end to end against the Dockerized backend: spawned an instance
  with `humanize=True`, typed into a real form field (`httpbin.org/forms/
  post`), clicked a radio button — the click landed exactly on target
  (`hit_is_target: true`) and the page state actually changed
  (`input.checked === true`), confirming these are real, effective, trusted
  events and not just added latency.

### Known limitations, stated rather than hidden

- This is a **model**, not a replay: a single Bezier curve with sampled
  timing is a large step up from an instant teleport, but it will not fool a
  detector that fingerprints real human motion in detail (acceleration/
  deceleration shape, correlated micro-tremor). None of the 10 public
  detectors in `BOT_DETECTION_REPORT.md` test this; some commercial
  anti-bot stacks do.
- `scroll_page` is **not yet humanized** — deliberately scoped out.
  `scroll_position.py` is a carefully tuned, single-round-trip module (one JS
  call arms an end-of-scroll latch and scrolls); a stepped wheel-event
  rewrite risks destabilizing it and needs its own pass, not a rushed
  addition.
- Click-dwell sample size is thin (n=3) — trusted for pacing, not for tail
  shape. A longer/second recorded session would firm this up.

### Near-term follow-ups (not started)

- **Visible cursor overlay for headed/observed sessions.** CDP mouse events
  never move the OS's actual pointer sprite — that's inherent to how CDP
  input injection bypasses the OS input queue, not fixable by a flag, and
  irrelevant for a *remote* viewer anyway (workstream 3 below only ever sees
  rendered pixels). The fix is a synthetic in-page cursor: a small
  `position: fixed` element updated at each path point during a humanized
  move. Nodriver already has an adjacent primitive
  (`Tab.flash_point()`/`Element.flash()`, used by every `mouse_click()` as a
  brief highlight) — extending that to a persistent moving dot is a small,
  low-risk addition and the natural next step, since it also makes workstream
  3's remote steering legible (a human watching a screencast needs to see
  where the automation is about to click). The text caret needs no such
  work — real key events already drive Chrome's native caret.
- Recording a second, longer trace (ideally a different site/task shape) to
  thicken the thin distributions (click dwell, `modifier`/`delete` key
  classes) and validate the model generalizes past one 65-second session.

## 3. Human-in-the-loop remote steering — proposed, not started

Two related but separable pieces:

### 3a. A "need a human" MCP tool

A new tool (working name `request_human_input`) the agent calls when it hits
something it cannot or should not push through itself: a login form, a 2FA
code, a CAPTCHA the stealth layer didn't clear, a payment step. Sketch:

- `request_human_input(instance_id, reason: str, blocking: bool = True)` —
  raises the request, optionally blocking the tool call until resolved (with
  a sane timeout) or returning immediately with a request id a caller polls.
- Surfaces on whatever front end 3b builds, tied to the instance so the human
  is looking at (and can act on) the actual live tab, not a re-navigated
  copy.
- Resolution needs a defined shape: did the human complete the step, decline,
  or time out — the agent's next tool call should be able to tell which.
- Open question worth deciding before building: does this live purely in the
  MCP layer (a tool that just parks and polls a shared state store) or does
  it need backend-side awareness (a queue, a webhook to the front end)? Leans
  toward the latter once 3b exists, since "raise an event a browser-based
  human can see live" is not something a stdio/HTTP tool call alone can push.

### 3b. Web dashboard for live viewing + control

A small companion web app, likely its own service in `docker-compose.yml`
(separate from the MCP backend, reusing its browser instances rather than
duplicating them):

- **Session list**: `list_instances()` already exists; a dashboard lists live
  instances and lets a human pick one.
- **Live view**: stream the selected tab's rendering to the browser. Two
  candidate approaches, not yet decided between:
  - CDP `Page.startScreencast` (JPEG/PNG frame push over the existing CDP
    connection) → relay frames to the dashboard over a WebSocket. Simple,
    proven (this is what chrome://inspect and several cloud-browser products
    use), works even for a fully headless instance.
  - A real WebRTC stream (as the user suggested) — better latency/quality for
    continuous "watch it work" use, more moving parts (signaling, ICE, a
    media pipeline Chrome doesn't expose directly over CDP — would likely
    still originate from screencast frames re-encoded into a WebRTC track,
    or from a virtual-display + ffmpeg capture path). Worth prototyping the
    screencast route first and only reaching for WebRTC if latency proves to
    matter.
- **Control**: forward the dashboard viewer's mouse/keyboard events back as
  `Input.dispatchMouseEvent`/`dispatchKeyEvent`/`insertText` on the same
  tab's CDP connection — the inverse of the screencast.
- **Critical constraint carried over from this codebase's own architecture**
  (see `CLAUDE.md` on `cdp_transport`/`element_resolution`): this must NOT
  open a second raw CDP connection alongside the backend's own. It has to be
  a capability added *inside* the existing backend process (a WebSocket route
  reusing `BrowserManager`'s already-open `nodriver.Tab` per instance),
  otherwise it reintroduces exactly the concurrent-DOM-query and reply-
  delivery hazards this codebase's `cdp_transport`/`element_resolution`
  modules exist to prevent.
- Ties into 3a: the dashboard is where a `request_human_input` call actually
  surfaces and gets resolved.

### Suggested order for workstream 3

1. Screencast-based live view only (read-only), reusing an existing tab
   connection — proves the "no second CDP connection" constraint is
   satisfiable and gives immediate value (watching what the agent is doing).
2. Input forwarding (mouse/keyboard) on top of the same connection — turns
   viewing into steering.
3. `request_human_input` tool + a minimal "action needed" queue/notification
   on the dashboard, wired to a specific instance's live view.
4. Revisit WebRTC only if screencast latency is measured to be a real
   problem for the steering use case.

---

## Related research: agent-facing MCP API design

`.sprints/1/research/modern-web-agent-patterns.md` — a survey of current vs.
bleeding-edge patterns for browser-automation MCPs, done independently of
the three workstreams above but relevant to where this fork's *tool surface*
itself should head next. Brief pointers, not a commitment to a workstream
yet:

- **Current mainstream** (Playwright MCP, Chrome DevTools MCP, Vercel's
  `agent-browser`): accessibility-tree/semantic snapshots with stable opaque
  refs instead of raw DOM or screenshots, interactive-only/scoped
  observations, observation **deltas** (resend only what changed since the
  last revision), batched multi-step `act()` calls so the LLM isn't round-
  tripped per keystroke/click, and selector/action **caching** so a repeat
  run skips inference entirely.
- **Bleeding edge**: **WebMCP** (a site exposes typed tools like
  `searchFlights(...)` directly, bypassing DOM grounding altogether when
  supported) and **discover → compile → replay** architectures (an
  exploratory trajectory becomes a validated, replayable recipe; only a
  postcondition mismatch falls back to the LLM) — both still experimental
  but explicitly what Chrome DevTools MCP and Stagehand are building toward.
- **Where this fork sits today**: closer to the "CDP-oriented" shape the
  research calls out as the thing to move away from — 94 largely
  one-primitive tools (`click_element`, `type_text`, `query_elements`, …)
  rather than a small task-oriented surface (`observe`/`act`/`extract`) with
  deltas and batching. Not a criticism to act on immediately — upstream's
  own design goals (a shared fleet-scale backend, exhaustive CDP access) are
  a different axis from this research's — but worth flagging as a candidate
  **workstream 4** if this fork's direction moves toward agent-driven use
  rather than tool-call-by-tool-call automation.
