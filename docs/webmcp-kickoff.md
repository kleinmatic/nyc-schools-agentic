# WebMCP — tomorrow's kickoff

Short note on where to start. Full brief: `docs/webmcp-task.md` (read §11 amendments first — they post-date the original draft).

## State of play (May 19, 2026 evening)

- Chrome Canary installed locally; `chrome://flags/#enable-webmcp-testing` enabled.
- I/O '26 developer keynote watched; transcript notes folded into `webmcp-task.md` §11.
- Three phases scoped (declarative annotations → `/compare` page → imperative JS). Phase 1 alone is a valid ship.

## First action tomorrow: install Modern Web Guidance

Google ships an official Claude Code plugin that gives the coding agent authoritative WebMCP knowledge (the `skills/modern-web-guidance/guides/webmcp/` directory in <https://github.com/GoogleChrome/modern-web-guidance>). Install it *before* touching templates so attribute names come from the spec, not guesswork.

```
/plugin marketplace add GoogleChrome/modern-web-guidance
/plugin install modern-web-guidance@googlechrome
/reload-plugins
```

Universal alternative if the plugin install hits issues: `npx modern-web-guidance@latest install`.

**Sanity check after install:** ask the agent "what attribute prefix does WebMCP use for declarative form annotations?" — answer should be authoritative, not hedged.

## Then: phase 1

`zoned.html` (formerly `find.html`) and `search.html` only. Two GET forms, three annotations (the `/zoned` form appears in both templates and shares one tool name). Diff is small.

- Annotate per the (now-authoritative) Modern Web Guidance examples.
- `make lint && make test`.
- Sanity-fetch the pages: each annotated form should have its tool attribute present.
- Commit per [feedback: commit each subtask](../../.claude/projects/-Users-kleinmatic-Code-nyc-schools-agentic/memory/feedback_commit_each_subtask.md).

## Validation

Old brief said `curl | grep`. New plan: use **Lighthouse agentic browsing** (the new category in Chrome DevTools for Agents) as the primary check — it validates WebMCP tool registrations, declarative form metadata, and accessibility tree readiness in one pass. The curl checks become smoke tests, not the contract.

## What to *not* expect on Canary today

Keynote phrasing was "Gemini in Chrome will **soon** support your WebMCP tools." The API (`window.webmcp.registerTool`, declarative attribute parsing) is probably live behind the flag; the consumer (Gemini-in-Chrome actually calling registered tools end-to-end) may not be. Don't gate phase 1 on a successful agent-driven invocation — gate on Lighthouse + DOM presence and revisit end-to-end when the consumer ships.

## Phases 2 and 3 (optional, same day or later)

- **Phase 2:** new `app/services/comparison.py` with `compare_schools(a_dbn, b_dbn) -> SchoolComparison`. New `/compare` page. Service layer rule: primitives in, Pydantic out, no Request/Context. ~2 hr at this model/effort tier.
- **Phase 3:** `app/web/static/webmcp-tools.js` with two imperative tools (`compare_schools` direct + `filter_results_by_level` client-side). Adds `?format=json` branch to `/compare` route. ~2 hr.

Both are valid stopping points after phase 1.

## Out of scope (file as follow-ups, don't scope-creep)

- `llms.txt` (different standard, same Lighthouse audit checks it).
- Touching `/mcp/` server (different audience).
- Registering the site in external WebMCP directories (discovery isn't a problem this task solves).
- Origin trial token (only enable in prod after local Lighthouse validation passes).
