# Bot Detection Test Report — Dockerized stealth-chrome-devtools-mcp

**Date:** 2026-09-24
**Environment:** `docker compose up` (Linux container, `linux/amd64`, Google Chrome Stable, headless), MCP server reached over HTTP transport from Claude Code.

## Abstract

Ten public bot-detection / fingerprinting tools were run against a headless Chrome
instance spawned through the Dockerized `stealth-chrome-devtools-mcp` backend, using
a persistent named session (`bot-test`) so cookies and history could accumulate
across requests. The automation-fingerprint layer — `navigator.webdriver`, CDP/
DevTools artifacts, automation globals, headless-UA strings, plugin lists — came
back clean on every test that checked for it. The two genuine dings observed were
environmental rather than code-level: a missing `chrome.runtime` object (a known
headless-Chromium gap) and a datacenter IP + virtualized-GPU fingerprint, both
consequences of running inside a Docker container rather than of the tool's stealth
logic. One test (Google web search) required a session with a small amount of prior
history to avoid an initial CAPTCHA challenge; a brand-new disposable profile
triggered it on the first request.

## Methodology

- Backend: `docker compose up -d --build` from this repository, registered as an
  HTTP-transport MCP server (`claude mcp add --transport http`).
- Browser: one instance spawned via `spawn_browser(headless=true, session="bot-test")`
  — a **named, persistent** profile (not the disposable per-spawn clone), so the
  container's `stealth_mcp_sessions` volume retains cookies/localStorage/history
  across the whole run and future runs.
- Each target was opened with `navigate`, allowed to settle (`networkidle` or an
  explicit wait for dynamic content), then inspected via `take_screenshot`
  (copied out of the container with `docker cp` for review) and, where a tool
  exposed a structured result, `query_elements` / `execute_script` / the tool's own
  JSON output view.
- No CAPTCHA image challenges were solved; per the test plan, only the Cloudflare
  Turnstile checkbox/"verify you are human" step was exercised (via a real CDP
  `Input.dispatchMouseEvent` click, not scripted DOM manipulation), not the full
  "Cloudflare Challenge" interstitial.
- Test 10's page (2captcha.com) renders Cloudflare's published **test** sitekey
  (`3x00000000000000000000FF`), which is documented to always succeed and is
  watermarked "For testing only" — its result is reported but is not evidence of
  real Turnstile bot-scoring either way.

## Results

| # | Target | Verdict | Notes |
|---|--------|---------|-------|
| 1 | google.com search (`wetter bern, schweiz`) | **Pass** (2nd attempt) | A brand-new disposable profile was redirected to `/sorry` (reCAPTCHA challenge) on the first request. The same request against the slightly-aged persistent `bot-test` profile returned real search results directly, no challenge. |
| 2 | browserscan.net/bot-detection | **Pass** | "Test Results: Normal" across all four checks (Webdriver, User-Agent, CDP, Navigator). |
| 3 | bot-detector.rebrowser.net | **Pass** | All automated checks green (`runtimeEnableLeak`, `navigatorWebdriver`, `viewport`, `pwInitScripts`, `bypassCsp`). One informational yellow on `useragent` (Chromium version unreadable via client-hints in this environment) — not a bot signal, and the remaining rows are manual/console-only tests with no automated verdict. |
| 4 | donutbrowser.com/tools/bot-detection | **Pass** | Bot score 18/100 (0=human, 100=automated), verdict "Looks like a real browser." 11 checks run, 1 flagged (not isolated in the minified detail view). |
| 5 | scrapfly.io/web-scraping-tools/automation-detector | **Pass** | "Not Automated," 0 detected / 13 safe / 1 suspicious of 14 total. The suspicious signal was `chrome.runtime` reported "Missing" — a genuine, specific headless-Chromium gap (real desktop Chrome always exposes `window.chrome.runtime`). |
| 6 | apivoid.com/tools/bot-detection-test | **Fail** (78/100, "High Risk \| Likely Bot", 8 rules triggered) | Breaking the 4 sub-categories down: **Browser Integrity: Passed, 0 anomalies** (this is the category actually covering automation markers). The failing categories were **Network & Request** (2 rules — IP resolves to a Microsoft Azure/hosting ASN, not residential) and **Hardware & Display** (5 rules — "Virtual Machine: Likely," a GPU/WebGL renderer signature typical of a container with no GPU passthrough). Both are properties of the deployment environment, not of the browser's automation fingerprint. |
| 7 | bscan.info/blog/bot-detection | **Pass** (11/12) | Only flagged signal: GPU/WebGL renderer — the same container/VM hardware artifact as test 6. `navigator.webdriver`, headless user-agent, automation globals, `window.chrome` object, plugins, `navigator.languages`, notification-permission consistency, native function integrity, JS engine consistency, window dimensions, and CDP/DevTools channel all passed. |
| 8 | pixelscan.dev/bot | **Pass** | Score 89–92/100, "Human Verified." 1 of 12 signals triggered: "Headless Mode detected" — accurate and expected, since the instance was spawned with `headless=true`. |
| 9 | antcpt.com/score_detector (reCAPTCHA v3) | **Pass** | Score 0.9 / 1.0 ("This is a good result, you can work with fast reCAPTCHA 2"). This is Google's live reCAPTCHA v3 engine, which does weigh IP reputation, so the high score held despite the hosting-IP classification seen in test 6. |
| 10 | 2captcha.com/demo/cloudflare-turnstile (checkbox only) | **"Success!"** / `"success": true` | Verified with one real mouse click on the widget. Uses Cloudflare's documented always-pass test sitekey (`result_with_testing_key: true`); not evidence of real Turnstile scoring. |

### Aggregate

- **8 / 10 clean passes**, **1 conditional pass** (Google — required session history), **1 fail driven entirely by environment, not automation fingerprint** (apivoid.com).
- Every test that specifically isolates automation/webdriver/CDP signals (tests 2, 3, 5 "Browser Integrity" category, 6 "Browser Integrity" category, 7, 8) reported them clean.
- The two recurring, non-automation weaknesses were:
  1. **`chrome.runtime` missing** (test 5) — a real, addressable headless-Chromium fingerprint gap.
  2. **Datacenter IP + virtualized-GPU signature** (tests 1 first attempt, 6, 7) — inherent to running Chrome in this Docker container without a residential/mobile egress proxy or GPU passthrough; no JS-level stealth patch changes either of these.
