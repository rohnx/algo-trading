# Fresh-conversation prompts

## 1. Agree the behavior before implementation

Use the installed Agent Skills plugin's `interview-me` and
`spec-driven-development` skills. Read
`orb/research/pcr-expiry-2026-10-23/README.md`, its sanitized evidence, the current
monitor, `GLOSSARY.md`, and `orb/tests/README.md` first.

I want to add a NIFTY PCR graph, an expiry-day indicator, and risk context based
on the risk table transcribed in the feasibility report. My proposed additional
confirmation is PCR > 1 for ORhB and PCR < 1 for ORlB. Treat these as my requested
rules, not as a statistically validated edge.

The cold tests establish that Shoonya supplies raw NIFTY option OI and same-day
per-contract history; Yahoo did not supply NIFTY/SENSEX option chains. A complete
PCR series and historical coverage have not been established. Do not repeat
authenticated probes unless a specific unresolved requirement needs one.

Interview me one question at a time, propose sensible defaults, and settle:

- OI versus volume PCR; selected expiry; all strikes versus a stated band;
  whether a band moves; observation cadence, completeness, and freshness.
- Which ORB/dual-ORB close is confirmed; strict threshold/equality behavior;
  the data eligible as of that close; informational display versus qualified
  signal versus a gate on existing alerts; missing/stale data behavior.
- Whether PCR is available only with Shoonya selected or also alongside Yahoo
  prices, and what connections/source provenance that permits.
- The graph's placement and historical/replay behavior; the session date used
  for the expiry badge; exact holiday-adjusted dates and monthly/weekly priority.
- Whether R is a risk-budget/position multiplier, a reward target, or something
  else; display a range versus a deterministic selection rule; the precise
  10:15 IST boundary and trigger timestamp. Do not silently choose midpoints.

Preserve existing monitor capabilities outside the agreed change. Finish with
the smallest coherent specification, examples for the risk-table cells, data
contracts, acceptance tests, and an incremental plan. Save the agreed result as
`orb/research/pcr-expiry-2026-10-23/SPEC.md`. Wait for my agreement on the behavior
before implementing the feature.

## 2. Build the agreed specification

Use Agent Skills' `source-driven-development`, `incremental-implementation`,
`test-driven-development`, and `frontend-ui-engineering`; apply
`api-and-interface-design` to the new data contracts. Implement the agreed
`orb/research/pcr-expiry-2026-10-23/SPEC.md`. Read the feasibility report and
current test ownership guide before changing code. If that specification is
missing or leaves a material strategy decision open, resolve it first.

Work within `orb/` in small complete slices: validated option/OI observations
and expiry metadata; PCR aggregation and persistence; confirmation and risk
policy; then graph/badge/labels. Keep raw provider observations, policy decisions,
and display distinct. Retain the current session-transition owner and integrate
with the approved connection policy rather than opening an accidental second
Shoonya socket.

Use total `oi` for an OI-based PCR, never `intoi` or index price volume. Expose the
provider, expiry, strike universe, coverage, and observation time. Price-only
updates must not make old OI look fresh. Handle missing strikes, zero denominators,
expiry rollover, holidays, and incomplete history explicitly. Do not present
partial-chain measurements as full PCR, fabricate past points, or use future
data to confirm an earlier close.

Preserve existing backend/UI behavior except the approved changes to
confirmation or alerts. Protect provider isolation, cache compatibility, atomic
processing of simultaneous candle closes, and notification deduplication. Place focused tests with their
responsibility owners and use the existing Playwright scenario runner for UI
verification. Add no order execution or credential logging. Finish with build,
relevant regression evidence, documentation, and an independent
`code-review-and-quality` review of correctness and complexity.

## 3. Optional independent review in a separate conversation

Use Agent Skills' `code-review-and-quality`. Review the PCR/expiry/risk changes
against `orb/research/pcr-expiry-2026-10-23/SPEC.md`, the feasibility evidence, and
the actual diff. Start read-only and report actionable findings with evidence.

Pay particular attention to time-aligned OI completeness, OI versus price
freshness, partial versus full PCR, use of completed history, no look-ahead in
replay, source/expiry rollover, holiday and monthly classification, all risk-table
boundaries, and the exact scope of any changes to ORB signals or Telegram
eligibility. Check test independence and whether ownership actually became
clearer. Separate provider facts, user-chosen strategy policy, and unsupported
assumptions; do not infer trading effectiveness from passing software tests.
