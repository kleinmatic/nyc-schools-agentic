# Task: Add WebMCP support to nyc-schools-agentic

A task brief for the Claude that edits this repo. Scoped, sequential, opinionated. Read this entire file before touching code.

---

## 1. Context

**WebMCP** is a proposed open web standard announced at Google I/O 2026 (May 19) that lets a website expose structured tools to in-browser agents. Spec page: <https://developer.chrome.com/docs/ai/webmcp>. Origin trial begins in **Chrome 149**; today (May 2026) it's behind `chrome://flags/#enable-webmcp-testing`.

Two APIs:

- **Declarative** — annotate standard HTML form elements with WebMCP attributes. Each annotated form becomes a tool the browser agent can call.
- **Imperative** — register tools in JavaScript (for actions that don't fit a form: comparisons, multi-step flows, page-state manipulation).

**Why we care.** This site is the kind of publisher surface WebMCP is designed for: a structured, data-rich civic site with real GET forms (`/find`, `/search`) that map cleanly onto agent intents ("find me the school zoned to this address"). Being on the WebMCP map from day one is cheap and strategically useful — when Gemini-in-Chrome users are browsing the site, they get tool-driven help instead of HTML scraping or hallucination.

**What WebMCP does *not* solve** (don't try to fix these in this task):
- No identity / verification (anyone can claim to expose tools; trust comes from the user already being on the domain).
- No cross-origin discovery (a Gemini user who has never visited this site doesn't find us via WebMCP).
- No coordination with this site's existing MCP server at `/mcp/` (different protocol, different audience — see §3 boundaries).

---

## 2. Read first

Before any edits, read these in order:

1. **The Chrome WebMCP spec page**: <https://developer.chrome.com/docs/ai/webmcp>. The attribute names and JS API surface I sketch below are best-effort as of writing; **the spec is the source of truth**. If anything in this brief disagrees with what the spec page currently says, the spec wins. Note the version / date you read.
2. **`CLAUDE.md`** in the repo root. Pay attention to the **"unified service layer"** section — every data-access operation lives once in `app/services/` as transport-agnostic Python; web, MCP, and any future surface are adapters. WebMCP's declarative API does not need a new service; the imperative API will need one new service function (see §6).
3. **`app/web/templates/find.html`** and **`app/web/templates/search.html`** — the two existing GET forms you'll annotate first.
4. **`app/web/routes.py`** lines 86–122 — the handlers backing those forms. You won't change them; understand their contract.

Optional but useful:
- `app/mcp_server/server.py` — see how the existing 14 MCP tools are organized. WebMCP tools are **separate** from these; don't conflate.
- `app/services/` — the keystone layer. New service functions go here.

---

## 3. Scope boundaries — what NOT to do

These are equal in weight to the things-to-do list. Violating any of these means the PR is wrong even if the WebMCP part works.

- **Do not touch `app/mcp_server/server.py` or any MCP tool definitions.** WebMCP and the existing `/mcp/` server are different artifacts for different audiences. The MCP server is consumed cross-process by chat clients (Claude Desktop, LibreChat, etc.). WebMCP is consumed in-browser by Gemini-in-Chrome. They coexist; they do not merge.
- **Do not restructure existing templates or routes.** Annotate the existing forms in place. If a form is currently `<form action="/find" method="get">`, it stays that shape — you add WebMCP attributes, nothing more.
- **Do not introduce a new framework, build step, or static-asset bundler.** This site is Jinja2 + a small amount of inline JS / CSS today. If you need JS for the imperative API, drop it in `app/web/templates/partials/` or as a small static file served by FastAPI; don't add esbuild / vite / webpack.
- **Do not depend on or reference any other repository.** This task is self-contained. Pretend nothing else exists. (There is coordination happening in a sibling project; the editing Claude does not need to know about it.)
- **Do not add WebMCP-only endpoints.** Every WebMCP-exposed action must correspond to a real HTML page or form a human can also use. The agent surface and the human surface are the same surface — that's the whole point of the declarative API.
- **Do not register the site in any external registry / directory as part of this task.** Discovery is out of scope.
- **Do not enable the origin trial token** in production until §8 (testing) has been done locally and verified. An untested origin-trial token means random Chrome users execute untested tool definitions in your name.

---

## 4. Big picture of what you're going to ship

Three phases. Ship each as its own commit so review is incremental.

| Phase | What | Files touched | Effort |
|---|---|---|---|
| **1** | Declarative WebMCP on the two existing GET forms | `app/web/templates/find.html`, `app/web/templates/search.html` | ~30 min |
| **2** | A `compare_schools` service function + a `/compare` HTML page (also WebMCP-annotated) | `app/services/comparison.py` (new), `app/web/routes.py`, `app/web/templates/compare.html` (new) | ~2 hr |
| **3** | Imperative WebMCP JS that registers `compare_schools` and `filter_results` as scripted tools | `app/web/static/webmcp-tools.js` (new), inclusion in `app/web/templates/base.html` | ~2 hr |

Phases 2 and 3 are optional — if you finish phase 1 and want to stop, that's a valid increment. Phases 2 and 3 light up imperative use cases that don't fit forms.

---

## 5. Phase 1 — Declarative WebMCP on existing forms

### Files to edit

1. `app/web/templates/find.html` — single form, address input → zoned schools.
2. `app/web/templates/search.html` — primary search form + an inline secondary `/find` form (annotate both).

### What the annotations look like

**Verify the exact attribute names against the spec page before committing.** The shape I'm sketching is what I'd expect based on the I/O announcement; Chrome 149's actual attribute prefix may be `data-mcp-*`, `mcp-*`, or scoped via a custom element. Adjust accordingly.

Conceptually, each annotated form needs:

- A **tool name** the agent will see (snake_case, action-verb, e.g. `find_zoned_schools_by_address`).
- A **tool description** the agent uses to decide when to invoke it (one sentence, plain English, written from the agent's perspective — *"Find the NYC public schools zoned to a given street address."*).
- A **parameter description** for each input (what shape of value, with one concrete example).

### Concrete diff for `find.html` (assuming `data-mcp-*` prefix — verify)

```html
<form action="/find"
      method="get"
      class="mb-10"
      data-mcp-tool="find_zoned_schools_by_address"
      data-mcp-description="Find the NYC public schools zoned to a given street address. Returns the zoned elementary, middle, and (where applicable) high school for that address.">
  <input
    name="address"
    type="text"
    placeholder="e.g. 715 Carroll St., Brooklyn"
    required
    data-mcp-param-description="A street address located in any of the five boroughs of NYC. Include the borough name. Example: '715 Carroll St., Brooklyn'.">
  <button type="submit" class="mt-3 px-4 py-2 bg-slate-900 text-white text-sm rounded-lg hover:bg-slate-700">Find zoned schools →</button>
</form>
```

### Concrete diff for `search.html`

Two forms in this file. Annotate both:

```html
<!-- primary search-by-name form (line 21) -->
<form action="/search"
      method="get"
      class="mb-6"
      data-mcp-tool="search_schools_by_name"
      data-mcp-description="Search NYC public schools by name or DBN. Returns a ranked list of matching schools with summary metrics.">
  <input
    name="q"
    type="text"
    placeholder="School name or DBN…"
    data-mcp-param-description="Full or partial school name (e.g. 'Bronx Science') OR a 6-character DBN (e.g. '15K321').">
  <!-- existing button + rest of form -->
</form>

<!-- secondary /find form (line 45) -->
<form action="/find"
      method="get"
      data-mcp-tool="find_zoned_schools_by_address"
      data-mcp-description="Find the NYC public schools zoned to a given street address.">
  <input
    name="address"
    type="text"
    data-mcp-param-description="A street address in NYC, including borough.">
  <button type="submit" ...>Find zoned schools →</button>
</form>
```

Both `/find` annotations expose the **same tool name** (`find_zoned_schools_by_address`). That's intentional — a single logical tool, two HTML entry points. The spec should tolerate this; if it doesn't, drop the annotation on the secondary form (the primary in `find.html` is the canonical one) and add a comment in the template explaining why.

### Verification (before merging)

- `make lint` and `make test` still pass.
- `curl -s http://localhost:8000/find | grep -c data-mcp-tool` returns at least `1`.
- `curl -s http://localhost:8000/search | grep -c data-mcp-tool` returns at least `2`.
- No visual change to the page in a non-WebMCP browser (the attributes should be invisible).

### Commit

```
[add] WebMCP declarative annotations on /find and /search forms

Annotates the two existing GET forms with WebMCP attributes so Gemini-in-
Chrome (and any future browser-agent) can invoke them as structured
tools. No behavioural change for humans; the attributes are no-ops
outside a WebMCP-aware browser.

Attribute syntax follows the spec at developer.chrome.com/docs/ai/webmcp
as of <date you verified>. If Chrome 149 ships different attribute
names, update accordingly — the service layer doesn't change.
```

---

## 6. Phase 2 — `compare_schools` service + `/compare` page

This phase adds a new operation that maps cleanly to a human use case ("how does PS 321 stack up against PS 139?") and exposes it as both a human-browseable page AND a WebMCP-declarative tool.

### Step 6.1: Service function

Create `app/services/comparison.py`. Follow the project's "transport-agnostic primitives in, Pydantic out" convention.

```python
"""Side-by-side comparison of two NYC public schools."""
from __future__ import annotations

from pydantic import BaseModel

from .schools import get_school   # or wherever the single-school getter lives — verify


class SchoolComparisonRow(BaseModel):
    metric: str           # human-readable label, e.g. "Chronic absenteeism"
    a_value: str | None   # already-formatted string; None means "not reported"
    b_value: str | None
    better: str | None    # "a", "b", or None (when ambiguous or not directional)


class SchoolComparison(BaseModel):
    a_dbn: str
    b_dbn: str
    a_name: str
    b_name: str
    rows: list[SchoolComparisonRow]


def compare_schools(a_dbn: str, b_dbn: str) -> SchoolComparison:
    """Return a side-by-side comparison of two schools across the same metric
    set used elsewhere on the site (graduation rate, chronic absenteeism,
    ELA / math proficiency, Regents pass rates, ENI, total enrollment, etc.).
    """
    # Implementation: call get_school for each, project the shared metric
    # set into SchoolComparisonRow objects, decide `better` per-metric based
    # on directional metric metadata (higher-is-better vs lower-is-better).
    ...
```

The exact metric set should match what the school-detail page (`/school/{dbn}`) already shows — don't introduce metrics that don't appear elsewhere. The point is "show me both schools' versions of the same numbers, side by side."

### Step 6.2: Web adapter

Add to `app/web/routes.py`:

```python
@router.get("/compare", response_class=HTMLResponse)
async def compare_page(request: Request, a: str = "", b: str = ""):
    if not a or not b:
        return templates.TemplateResponse("compare.html", {
            "request": request,
            "comparison": None,
        })
    comparison = comparison_service.compare_schools(a, b)
    return templates.TemplateResponse("compare.html", {
        "request": request,
        "comparison": comparison,
    })
```

### Step 6.3: Template

Create `app/web/templates/compare.html`. Two-input form (DBN of school A, DBN of school B). When both are present and the query succeeds, render the comparison table below the form.

Annotate the form:

```html
<form action="/compare"
      method="get"
      data-mcp-tool="compare_schools_side_by_side"
      data-mcp-description="Render a side-by-side comparison of two NYC public schools across graduation rate, chronic absenteeism, exam proficiency, Regents pass rates, ENI, and enrollment. Useful when the user is choosing between two schools or evaluating one against a known reference.">
  <input
    name="a"
    type="text"
    placeholder="First school DBN (e.g. 15K321)"
    required
    data-mcp-param-description="The DBN (6-character school identifier) of the first school. Get one from search_schools_by_name or find_zoned_schools_by_address.">
  <input
    name="b"
    type="text"
    placeholder="Second school DBN"
    required
    data-mcp-param-description="The DBN of the second school to compare against the first.">
  <button type="submit">Compare</button>
</form>
```

Note the tool description tells the agent how to **get** the DBNs (chain from the other tools). This composition hint matters — without it the agent might prompt the user for raw DBNs, which is a poor experience.

### Step 6.4: Tests

In `tests/`, add a service-layer test that constructs known DBNs and asserts the comparison rows are populated and directional. Mirror the style of existing service tests if any exist; if not, see `tests/conftest.py` for fixtures.

### Commit

```
[add] compare_schools service + /compare page with WebMCP annotation

New service function returns a structured side-by-side comparison of two
schools across the standard site metric set. New /compare HTML page
exposes it to humans and (via WebMCP attributes) to browser agents. The
agent-side tool description includes a composition hint pointing at
search_schools_by_name / find_zoned_schools_by_address as DBN sources.
```

---

## 7. Phase 3 — Imperative WebMCP tools

The imperative API earns its keep when you want to expose actions that don't fit a `<form>`. Two specific candidates for this site:

1. **`compare_schools`** — even though phase 2 exposed it as a form, the imperative version takes structured DBN args directly (no intermediate page load), which is what a chained agent flow wants. Both can coexist.
2. **`filter_results_by`** — on `/search` and `/neighborhood/{nta}` pages where a result list is rendered, expose a JS tool that filters / sorts the rendered list in place. Useful for "show me only the elementary schools" or "sort by graduation rate" without a server round-trip.

### Where the JS lives

Create `app/web/static/webmcp-tools.js`. Mount it via FastAPI's `StaticFiles` in `app/main.py` if not already mounted; include it in `app/web/templates/base.html` with a `<script defer>` tag so it loads on every page.

### What the JS does (sketch — verify against spec)

```javascript
// app/web/static/webmcp-tools.js
//
// Imperative WebMCP tool registrations for nyc-schools-agentic.
// Per the spec at developer.chrome.com/docs/ai/webmcp, browser agents
// see these as callable tools in addition to the form-based ones.

(function () {
  if (!window.webmcp) return;  // graceful no-op outside WebMCP-aware browsers

  // 1) Direct DBN-to-DBN comparison (no form round-trip).
  window.webmcp.registerTool({
    name: 'compare_schools',
    description: 'Compare two NYC public schools side-by-side by DBN. ' +
      'Returns the same metric set as the /compare page but as structured data.',
    parameters: {
      a_dbn: { type: 'string', description: 'DBN of first school, e.g. "15K321".' },
      b_dbn: { type: 'string', description: 'DBN of second school.' },
    },
    handler: async ({ a_dbn, b_dbn }) => {
      const res = await fetch(`/compare?a=${encodeURIComponent(a_dbn)}&b=${encodeURIComponent(b_dbn)}&format=json`);
      if (!res.ok) throw new Error(`compare_schools failed: ${res.status}`);
      return await res.json();
    },
  });

  // 2) Filter the currently-rendered result list by school level.
  //    Only meaningful on pages that render a result list.
  window.webmcp.registerTool({
    name: 'filter_results_by_level',
    description: 'On a page showing a list of schools, filter the visible ' +
      'rows to a single school level (elementary, middle, high, k-12). ' +
      'Has no effect on pages without a school list.',
    parameters: {
      level: { type: 'string', enum: ['elementary', 'middle', 'high', 'k-12'] },
    },
    handler: ({ level }) => {
      const rows = document.querySelectorAll('[data-school-level]');
      let visible = 0;
      rows.forEach(r => {
        const match = r.dataset.schoolLevel === level;
        r.style.display = match ? '' : 'none';
        if (match) visible++;
      });
      return { matched: visible, total: rows.length };
    },
  });
})();
```

For `compare_schools` to work imperatively you need the `/compare` route to optionally return JSON when `?format=json` is set (currently it returns HTML only). Add that branch in `routes.py`. For `filter_results_by_level` to work you need to add `data-school-level="..."` attributes on the result rows in `search.html`, `neighborhood.html`, and any other list-rendering template — small annotation, no logic change.

### Commit

```
[add] WebMCP imperative tools (compare_schools, filter_results_by_level)

Registers two JS-driven tools for browser agents. compare_schools mirrors
the /compare form but takes DBNs directly (no intermediate page); useful
in chained agent flows where DBNs are already in hand. filter_results_
by_level operates client-side on rendered school lists. Tool definitions
gracefully no-op in non-WebMCP browsers via the window.webmcp guard.

/compare route now returns JSON when ?format=json is set. Result rows in
search.html and neighborhood.html now carry data-school-level for the
client-side filter.
```

---

## 8. Testing

### Local manual test (do this before deploying)

1. **Enable the Chrome flag**:
   ```
   chrome://flags/#enable-webmcp-testing → Enabled → Relaunch
   ```
2. **Confirm the version**: Chrome must be ≥149 for the origin trial. Older Chromes have the flag but no implementation.
3. **Register for the Chrome origin trial** if the spec requires a token (check the spec page). If a token is required, add it as a `<meta http-equiv="origin-trial" content="...">` tag in `base.html`. Don't commit the token directly if it's tied to a specific origin and might leak; use an env var + Jinja injection.
4. **Run locally**: `uv run uvicorn app.main:app --reload`.
5. **Open Gemini-in-Chrome** (or whatever browser-agent surface Chrome 149 exposes) on the page.
6. **Verify tool surface**:
   - On `/find`, ask the agent: *"What schools are zoned to 715 Carroll Street, Brooklyn?"* — the agent should invoke `find_zoned_schools_by_address` directly, not type into the form character by character.
   - On `/search`, ask: *"Search for Bronx Science."* — should invoke `search_schools_by_name`.
   - On `/compare`, ask: *"Compare 15K321 and 13K282."* — should invoke `compare_schools_side_by_side` (declarative) or `compare_schools` (imperative — verify which path it takes).
7. **Verify the no-WebMCP fallback**: visit the same pages in Safari or a Chrome without the flag. The forms must work exactly as before. The script must not throw.

### Automated test

In `tests/test_webmcp.py`, add a test that fetches each annotated page and asserts the WebMCP attributes are present:

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.parametrize(
    "path, expected_tool",
    [
        ("/find", "find_zoned_schools_by_address"),
        ("/search", "search_schools_by_name"),
        ("/compare", "compare_schools_side_by_side"),
    ],
)
def test_webmcp_tool_annotation_present(path, expected_tool):
    resp = client.get(path)
    assert resp.status_code == 200
    assert f'data-mcp-tool="{expected_tool}"' in resp.text
```

This is intentionally low-grade — it doesn't validate the spec, it just confirms we haven't accidentally dropped the annotations during a template refactor. Run via `make test`.

### Deploy

Standard `fly deploy --remote-only`. No new secrets, no new env vars (unless an origin trial token is in play). The site is live with WebMCP support the moment the deploy completes; only Chrome 149+ users with the flag will see it.

---

## 9. Things to watch / open questions

- **Spec stability.** Origin trial means the API will change. Expect to revisit attribute names, JS API shape, and possibly tool registration mechanics every Chrome milestone for ~6 months. Subscribe to the WebMCP spec page or its tracking issue.
- **Origin trial token rotation.** If a token is in use, it expires (typically with the milestone). Add a calendar reminder.
- **Tool composition surface.** When `compare_schools` exists as both declarative and imperative, agents might pick one over the other unpredictably. Watch real Gemini behaviour and prune one if it's not adding value.
- **Verification.** WebMCP today doesn't authenticate that *this* site is who it says it is. If a future spec adds signed agent-card-like metadata, layer it on. The site's existing AGPL footer + GitHub source link is the current trust anchor.
- **Coexistence with the `/mcp/` MCP server.** They're independent — don't try to share code between them. If you find yourself tempted to factor out a shared "tool schema" abstraction, stop. The two protocols have different shapes; a premature abstraction will rot fast.

---

## 10. References

- WebMCP spec: <https://developer.chrome.com/docs/ai/webmcp>
- Chrome flag: `chrome://flags/#enable-webmcp-testing`
- Origin trial: (link from spec page once registration opens)
- Google I/O 2026 announcement post: (link from spec page)
- This repo's `CLAUDE.md` for architectural conventions (service layer, adapter pattern, AGPL footer requirement, deploy mechanism)

---

## 11. Updates from the I/O '26 developer keynote (May 19, 2026)

Captured after the brief above was drafted. Treat as amendments; the phase structure stands.

### 11.1 Use Modern Web Guidance instead of guessing attribute names

Google launched **Modern Web Guidance**, a curated, expert-vetted set of agent skills covering modern web platform features including WebMCP. In the keynote demo, Antigravity used it to implement WebMCP tools on a car-configurator site from a single prompt. It's installable one-click in Antigravity *and* "as a ready-made package of skills for other coding tools." Before phase 1, check whether a Claude Code-compatible distribution exists; if so, install it. This replaces the "verify attribute names against the spec page" guard in §5, §6, §7 — the skill is what the spec page would tell you, in a form the coding agent can act on directly.

### 11.2 Validate with Lighthouse "agentic browsing," not just curl

**Chrome DevTools for Agents** ships a new Lighthouse category called **agentic browsing**. It validates WebMCP tool registrations, checks that forms carry the declarative metadata agents need, audits `llms.txt`, and re-runs the familiar a11y audits (agents navigate via the accessibility tree, so every ARIA fix is dual-purpose). Available for Antigravity and 20+ other coding agents, so a Claude Code integration likely exists. Use this in §8 as the **primary** automated check; demote the `curl | grep` checks to "did we obviously break something" smoke tests.

### 11.3 Don't expect Gemini-in-Chrome to invoke tools on day one

The keynote phrasing was: "Gemini in Chrome will soon support your WebMCP tools, building on the active experiments we're running with our ecosystem partners." "Soon" ≠ "shipped." On Chrome Canary today the *API* may be live (the flag exposes `window.webmcp` and tool registration) but the *consumer* (Gemini-in-Chrome actually calling your registered tools end-to-end) may not be. Calibrate the §8 manual test plan accordingly:
- **What you can probably verify on Canary today:** the page parses without errors, `window.webmcp.registerTool` calls succeed, the declarative attributes are present in the DOM, Lighthouse agentic-browsing audit passes.
- **What might have to wait:** end-to-end "ask Gemini-in-Chrome a question on the page and watch it invoke the tool."

Don't block phase 1 ship on the latter. Ship the annotations; revisit end-to-end the moment Gemini-in-Chrome's WebMCP consumer lands.

### 11.4 Imperative tool shape — confirmed by the keynote demo

Keynote narration on the car-configurator: *"Antigravity has implemented an imperative tool called Update Car Configuration, with all configuration options listed in the schema definition. Now Gemini in Chrome can use this specific targeted WebMCP tool for the job."* The sketch in §7 (a `registerTool({ name, description, parameters, handler })` shape with all params declared in the schema) is structurally right. Modern Web Guidance (§11.1) will give the exact API.

### 11.5 Accessibility-tree audit, included for free

Because agents navigate via the accessibility tree, fixing any a11y issues Lighthouse surfaces on `/find`, `/search`, and `/compare` directly improves agent reliability. Don't break this out as a separate task — just don't merge any phase if its Lighthouse a11y score regressed.

### 11.6 `llms.txt` — adjacent, out of scope for this task, worth a follow-up

The same Lighthouse audit checks for **`llms.txt`**, a proposed standard for "giving models a clear map of your site's content." This site doesn't have one. It's *not* part of the WebMCP task — different standard, different audience (any LLM crawler, not just in-browser agents) — but if the Lighthouse run in §8 flags its absence, file a follow-up issue rather than scope-creeping into this PR.
