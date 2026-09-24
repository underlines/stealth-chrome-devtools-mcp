# Web Agent Patterns

Research for current best practices vs. bleeding edge developments.

Current pattern:

**Observe → Decide → Execute → Verify**, with a **deterministic fast path** that bypasses the LLM whenever possible.

The important change versus early browser automation is that the browser layer is becoming an **agent-facing semantic runtime**, not merely Playwright/CDP exposed through MCP.

## Where the state of the art is in 2026

The clearest consensus is:

| Technique                           | Status                               | Why                                                              |
| ----------------------------------- | ------------------------------------ | ---------------------------------------------------------------- |
| Raw HTML/DOM → LLM                  | Avoid                                | Extremely noisy; poor token/semantic ratio                       |
| Accessibility/ARIA tree             | **Mainstream default**               | Compact, semantic, directly maps to actionable elements          |
| Stable element refs/UIDs            | **Mainstream default**               | Agent does not invent CSS/XPath selectors                        |
| Filtered/scoped observations        | **Mainstream**                       | Only interactive elements / subtree / matching region            |
| Separate “read” vs “interact” views | **Strong pattern**                   | Markdown/text for information, AX tree for controls              |
| Batch actions                       | **Strong pattern**                   | Avoid LLM round-trip between deterministic steps                 |
| Observation diffs                   | **Emerging → strong**                | Only tell model what changed                                     |
| Structured extraction               | **Strong pattern**                   | Return schema-shaped data instead of whole page                  |
| Action/selector caching             | **Production pattern**               | First run uses AI; repeat runs become deterministic              |
| Workflow compile/replay             | **Emerging strongly**                | Turn successful exploratory trajectory into reusable automation  |
| Screenshots/vision                  | Fallback                             | Essential for canvas/custom UI, but expensive/less deterministic |
| WebMCP / site-exposed tools         | **Bleeding edge / likely important** | Completely bypasses UI grounding when supported                  |

Playwright MCP now explicitly uses accessibility snapshots with refs instead of screenshots or DOM. It supports subtree/depth-limited snapshots and `browser_find`, which returns only matching nodes and context rather than the full page. ([Playwright][1])

Chrome DevTools MCP independently converged on essentially the same pattern: accessibility snapshots with UIDs, `fill_form` batching, optional suppression of post-action snapshots, and screenshot only when useful. ([GitHub][2])

Vercel's current `agent-browser` goes further with **interactive-only**, compact, depth/scoped snapshots, persistent refs across surviving DOM nodes, and `--delta` observations that return only structural changes after the first state. ([GitHub][3])

### One important nuance: don't blindly throw HTML away

A 2026 study specifically revisited observation reduction. It found accessibility trees generally help smaller/weaker models, while stronger models with larger reasoning budgets can sometimes exploit information in detailed HTML that AX trees removed. Diff-based history was a particularly attractive compromise. ([arXiv][4])

So I would **not** hard-code:

> AX tree = good; HTML = bad.

Instead:

> **Default to compact semantics and progressively reveal more detail.**

That is a much better MCP design.

---

# The observation layer is the biggest opportunity

Instead of:

```text
get_dom() → 20k tokens
```

I would make observation explicitly queryable:

```text
observe(
  mode="interactive",
  query="flight search form",
  scope="main",
  delta=true,
  max_nodes=80
)
```

Return something like:

```text
revision: 17
url: https://...

@e12 textbox "From"
@e13 textbox "To"
@e14 button "Search"
@e18 link "Google Flights"
```

Not:

```html
<div class="some-google-generated-class...">
...
```

### Useful observation modes

I'd support roughly:

```text
interactive
content
structure
full
visual
```

Where:

**`interactive`**
Only controls + small amount of surrounding context:

```text
@e12 textbox "Departure"
@e13 textbox "Arrival"
@e14 button "Search"
```

**`content`**
Rendered page converted to Markdown/readable text, without element plumbing.

This split already appears in newer tools: `agent-browser` has its compact snapshot path for interaction and a separate `read` path geared toward rendered content/Markdown. ([GitHub][5])

**`structure`**
AX tree with headings, regions, tables, controls, states.

**`full`**
Pruned DOM + AX + relevant attributes. Expensive escape hatch.

**`visual`**
Screenshot, preferably **cropped to a region/ref**, potentially with Set-of-Mark annotations.

---

# Don't resend unchanged state

This is probably the most obvious remaining token saving.

If state 17 was:

```text
@e10 textbox "From"
@e11 textbox "To"
@e12 button "Search"
```

and after filling From only this changed:

```text
~ @e10 textbox "From" value="Bern"
```

there is no reason to resend everything else.

Vercel's current implementation now does exactly this: full baseline followed by `unchanged` or structural deltas, reverting to a full snapshot if the delta is too large. ([GitHub][6])

For your own MCP I'd make this a first-class protocol concept:

```text
revision: 42
base_revision: 41

changes:
  - update @e10 value="Bern"
  - add @e31 listbox "Suggestions"
```

And make actions optionally require the revision:

```text
act(revision=42, ...)
```

This also gives you **optimistic concurrency protection against stale observations**.

---

# Push deterministic work underneath the LLM

This is the other major architectural change.

Your example currently requires something like:

```text
LLM → fill
LLM → fill
LLM → click
LLM → wait
LLM → inspect
```

But once the model understands the form, most of this requires zero additional intelligence.

Make one tool support:

```text
act(
  revision=42,
  actions=[
    fill("@e10", "BER"),
    fill("@e11", "BKK"),
    click("@e12")
  ],
  wait_until={
    url_changes: true,
    network_quiet: true
  },
  return="delta"
)
```

The MCP executes that whole micro-plan.

Both Playwright MCP and Chrome DevTools MCP already expose multi-field `fill_form`, which is the simplest example of this direction. ([GitHub][7])

Stagehand v4 explicitly advertises batched commands alongside agent-oriented primitives. ([Stagehand][8])

The important distinction is:

**LLM selects intentions/subgoals. Browser runtime handles mechanics.**

---

# Discovery → compilation → replay is becoming a real pattern

This part of your hypothesis is especially interesting.

Stagehand has moved strongly toward:

```text
AI discovers action
        ↓
resolved selector/action cached
        ↓
future calls execute without inference
```

Its cache validates the current page against the cached page state before replaying the action. On mismatch, it falls back to inference instead of blindly executing the stale selector. ([Browserbase][9])

Stagehand v4 now caches `act()`, `observe()` and `extract()` calls; a cache hit avoids the model call entirely. ([Browserbase][10])

There is also very recent academic work going beyond selector caching. **OdoBot**, published September 11, 2026, learns an application's behavioural model from successful demonstrations. The paper reports 44% and 80% lower token usage than two comparison agents on their Canvas LMS tasks. ([arXiv][11])

So I'd explicitly implement two states:

```text
EXPLORATION
    ↓ successful trajectory
COMPILE
    ↓
REPLAY
    ↓ validation failure
REPAIR / EXPLORATION
```

That is probably the right long-term architecture.

---

# WebMCP could change the problem entirely

This is the most important bleeding-edge development.

Chrome's **WebMCP** proposal lets a website expose:

```text
searchFlights({
    origin,
    destination,
    date,
    directOnly
})
```

instead of an agent doing:

```text
find From textbox
click
type
find To textbox
click
type
...
```

Chrome explicitly describes the goal as replacing DOM/screenshot actuation with structured tools exposed by the site. ([Chrome for Developers][12])

Chrome DevTools MCP now has experimental:

```text
list_webmcp_tools
execute_webmcp_tool
```

support. ([GitHub][13])

Stagehand also added WebMCP support this year. ([Browserbase][14])

So your browser MCP should absolutely be designed around this priority:

```text
1. WebMCP available?
      → invoke typed site tool

2. Otherwise semantic browser interaction
      → refs / accessibility tree

3. Otherwise richer DOM / browser state

4. Otherwise targeted vision
```

I would **not** make WebMCP mandatory yet—it is still experimental—but I would design the architecture around it now.

---

# Vision should be a targeted escape hatch

Vision is useful when the DOM/AX representation fundamentally fails:

* `<canvas>`
* maps
* diagrams
* graphical editors
* poorly accessible custom widgets
* icon-only interfaces
* visually meaningful layout

But don't switch the whole browser agent into screenshot mode.

Prefer:

```text
visual(ref="@e43")
```

or:

```text
visual(region="calendar")
```

rather than a 4K whole-page screenshot every turn.

Set-of-Mark approaches and systems such as OmniParser convert visual controls back into structured regions/IDs so the reasoning model doesn't have to perform raw x/y grounding itself. OmniParser remains actively developed; its 2026 update added a new interactive-region detector. ([GitHub][15])

So:

```text
semantic refs             ← default
        ↓ insufficient
annotated/cropped vision   ← fallback
        ↓
coordinate action internally
```

The LLM should preferably still say:

```text
click @v13
```

rather than:

```text
click x=817,y=643
```

---

# I would build these three variants

## 1. Semantic + Delta MCP — **the default I would build**

Underlying engine:

```text
Chromium
   ↓
CDP / Playwright-like automation
   ↓
Agent-oriented MCP
```

Expose only about **7–8 high-level tools**:

```text
navigate(...)
observe(...)
find(...)
act(...)
extract(...)
wait(...)
visual(...)
tabs(...)
```

Not 50 CDP methods.

`observe()`:

```text
observe(
    mode="interactive|content|structure|full",
    query?,
    scope?,
    depth?,
    delta=true,
    budget?
)
```

Key features:

* accessibility/semantic tree
* stable opaque refs
* persistent refs where possible
* revision IDs
* interactive-only filtering
* subtree/scoped observations
* text/regex `find`
* optional semantic search/reranker
* observation deltas
* Markdown content mode

`act()` should accept **multiple sequential operations** and perform all waiting/auto-waiting internally.

```text
act({
  revision: 41,
  actions: [
    {fill: "@e4", value: "BER"},
    {fill: "@e5", value: "BKK"},
    {click: "@e9"}
  ],
  expect: {
    url_matches: "flights",
    text: "Search results"
  },
  return: "delta"
})
```

This is the closest synthesis of Playwright MCP, Chrome DevTools MCP and current `agent-browser`. ([Playwright][1])

**For a general-purpose browser MCP, this would be my baseline.**

---

## 2. Semantic MCP + Learn/Compile/Replay — **best production design**

Add:

```text
recipe_compile(...)
recipe_run(...)
recipe_inspect(...)
```

During an unknown task:

```text
LLM
 ↓
observe
 ↓
act
 ↓
observe
 ↓
act
```

Successful trajectory becomes:

```text
search_flight(origin, destination, date)
```

internally:

```text
step 1:
  resolve textbox(role="textbox", name≈"From")
  fill $origin
  expect autocomplete

step 2:
  ...

step 5:
  click role=button name≈"Search"
  expect URL/result-state
```

Future call:

```text
recipe_run(
  "google_flights_search",
  {
    origin:"BER",
    destination:"BKK",
    date:"2026-12-02"
  }
)
```

One MCP call. **Zero planner LLM calls unless validation fails.**

On drift:

```text
step 3 validation failed

expected:
button(name≈Search)

observed:
button "Explore flights"

candidate:
@e41 confidence=.94
```

Only then return to the agent for repair.

This follows the Stagehand caching/self-healing direction and the newer behavioural-model research. ([Browserbase][9])

For enterprise/repeated workflows, **I'd choose this version**.

---

## 3. WebMCP-first Adaptive Browser — **bleeding-edge design**

Same MCP core, but observation resolution becomes:

```text
          ┌─ WebMCP tool
          │
Goal ─────┼─ structured extraction
          │
          ├─ semantic AX UI
          │
          ├─ DOM/JS/network state
          │
          └─ targeted vision
```

For every navigation:

```text
capabilities:
  webmcp:
    - searchFlights(...)
    - selectFlight(...)
  semantic_ui: true
  structured_data: true
```

Crucially, don't dump every WebMCP schema into context.

`agent-browser` already uses a nice pattern: advertise only tool names/descriptions initially and fetch the full schema only when the agent selects one. ([GitHub][5])

For example:

```text
page tools:
  searchFlights — Search available flights
  bookFlight    — Book selected itinerary
```

Then:

```text
describe_page_tool("searchFlights")
```

only if needed.

This avoids replacing DOM token waste with **tool-schema token waste**.

I'd consider this the architecture to prepare for, but not the only interaction mechanism yet.

---

# My preferred combined architecture

I'd actually combine all three:

```text
                         ┌───────────────┐
                         │     Goal      │
                         └───────┬───────┘
                                 │
                       capability discovery
                                 │
                ┌────────────────┴──────────────┐
                │                               │
          WebMCP available?                     │ no
                │ yes                           ▼
                ▼                        Semantic observe
         typed tool call                 AX / Markdown
                │                               │
                └───────────────┬───────────────┘
                                ▼
                              Plan
                         one SUBGOAL / macro
                                │
                                ▼
                         batched execute
                                │
                                ▼
                       postcondition check
                                │
                      ┌─────────┴────────┐
                      │                  │
                    valid              drift
                      │                  │
                      ▼                  ▼
                 delta only         re-observe
                      │
                      ▼
                    next
```

And independently:

```text
successful repeated trajectory
          │
          ▼
      compile/cache
          │
          ▼
 deterministic replay
          │
      validation fails
          │
          └────→ agent repair
```

## The main design rule

I would make the MCP API **task-oriented rather than CDP-oriented**.

Bad:

```text
get_dom
query_selector
click
focus
keyboard_type
sleep
get_dom
```

Better:

```text
observe
act
extract
```

And internally let the browser runtime perform all of:

```text
selector resolution
scrollIntoView
focus
click
typing
auto-waiting
navigation detection
DOM settling
postcondition checking
state diffing
```

That moves the **mechanical intelligence into deterministic code**, leaving only genuinely ambiguous choices to the model.

That is, in my view, the most important architectural shift between “browser automation exposed to an LLM” and a **browser runtime actually designed for agents**.

[1]: https://playwright.dev/mcp/snapshots?utm_source=chatgpt.com "Snapshots | Playwright"
[2]: https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/skills/chrome-devtools/SKILL.md?utm_source=chatgpt.com "chrome-devtools-mcp/skills/chrome-devtools/SKILL.md at main · ChromeDevTools/chrome-devtools-mcp · GitHub"
[3]: https://github.com/vercel-labs/agent-browser?utm_source=chatgpt.com "GitHub - vercel-labs/agent-browser: Browser automation CLI for AI agents · GitHub"
[4]: https://arxiv.org/abs/2604.01535?utm_source=chatgpt.com "Read More, Think More: Revisiting Observation Reduction for Web Agents"
[5]: https://github.com/vercel-labs/agent-browser/blob/main/skill-data/core/SKILL.md?utm_source=chatgpt.com "agent-browser/skill-data/core/SKILL.md at main · vercel-labs/agent-browser · GitHub"
[6]: https://github.com/vercel-labs/agent-browser/blob/main/CHANGELOG.md?utm_source=chatgpt.com "agent-browser/CHANGELOG.md at main · vercel-labs/agent-browser · GitHub"
[7]: https://github.com/microsoft/playwright-mcp "GitHub - microsoft/playwright-mcp: Playwright MCP server · GitHub"
[8]: https://www.stagehand.dev/?utm_source=chatgpt.com "The SDK for browser agents | Stagehand"
[9]: https://www.browserbase.com/blog/stagehand-caching?utm_source=chatgpt.com "How Caching Works in Stagehand (and Where It Breaks) | Browserbase | Browserbase"
[10]: https://www.browserbase.com/changelog/caching-configurable?utm_source=chatgpt.com "Configurable caching in Stagehand | Browserbase"
[11]: https://arxiv.org/abs/2609.13491?utm_source=chatgpt.com "Token Efficient Task Execution via Application Behavior Modeling for Web Agents"
[12]: https://developer.chrome.com/docs/ai/webmcp?utm_source=chatgpt.com "WebMCP  |  AI in Chrome  |  Chrome for Developers"
[13]: https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/tool-reference.md?utm_source=chatgpt.com "chrome-devtools-mcp/docs/tool-reference.md at main · ChromeDevTools/chrome-devtools-mcp · GitHub"
[14]: https://www.browserbase.com/changelog?utm_source=chatgpt.com "Changelog | Browserbase"
[15]: https://github.com/microsoft/OmniParser?utm_source=chatgpt.com "GitHub - microsoft/OmniParser: A simple screen parsing tool towards pure vision based GUI agent · GitHub"
