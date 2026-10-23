# Test responsibilities

Run the Python suite from the repository root:

```bash
.venv/bin/python -m unittest discover
```

Every module also runs independently. For example:

```bash
.venv/bin/python -m unittest orb.tests.market.test_quality
.venv/bin/python -m unittest orb.tests.application.test_dual_setup.DualSetupTests.test_source_switch_and_cache_restart_restore_both_setups_without_cross_provider_signals
```

The existing scoped command, `python -m unittest discover -s orb/tests`, also works.
Tests import the `orb` package. No module edits the import path or imports fixtures
from another executable test module.

Run the browser suite, inspect its case names, or reproduce one case independently:

```bash
node orb/tests/test_ui.cjs
node orb/tests/test_ui.cjs --list
node orb/tests/test_ui.cjs --case sources/retry
node orb/tests/test_ui.cjs --reverse
```

Browser cases live in `browser/` by responsibility: sessions, chart, sources,
motion, comparison returns/quotes/emphasis/groups, replay, and layout. Every case
creates its own browser context, synthetic data, local server, event stream, and
clock. The reversed run checks that earlier cases are not preparing later cases.

| Owner | Contract and test surface |
| --- | --- |
| `rules/` | Opening ranges, independent timeframe confirmation, and close-only comparison rules through the strategy/evaluator interfaces. |
| `market/` | Instrument registry, genuine five-minute aggregation, quote/opening metadata, observed coverage, disconnect gaps, and delayed provider clocks. |
| `sessions/` | Native/fallback composition, provider restoration, session-opening persistence, retention, and cache compatibility using isolated temporary storage. |
| `feeds/` | Real Yahoo/Shoonya adapters with controlled SDK/socket doubles: frame normalization, bounded transport, authentication, cancellation, capacity, and connection lifecycle. |
| `application/` | Monitor transitions and cross-boundary journeys: atomic dual agreement, primary/comparison separation, history admission, events, restart/source return, and notifier eligibility. |
| `transport/` | Real HTTP handler parsing, source requests, error responses, and SSE wire behavior through an in-memory connection. |
| `notifications/` | Alert formatting, credential precedence, durable enqueue/delivery, retry, deduplication, restart recovery, and concurrent persistence. |
| `test_package.py` | Resource/configuration/data paths, credential locations, and actual startup/shutdown wiring. |
| `browser/` | Independent scenarios for session navigation, source races, comparison display, replay, motion, geometry, and accessibility through the running dashboard. |

Put a rule case at its owning interface. Keep application journeys when the
guarantee requires several owners to cooperate. In particular, the source-switch
and cache-restart journey verifies that **both setups restore without signals
crossing providers**. Its breadth is intentional; it complements the focused
provider-restoration, cache, and dual-agreement checks.

`support/` contains ordinary synthetic clocks, candle builders, provider doubles,
HTTP connections, and notification samples. Tests create fresh instances and use
temporary directories. Helpers contain no test cases, discovery hooks, shared
running services, or credentials for a real account.

The former `test_features.py` collection has been dissolved: candle aggregation
and feed quality belong to `market/`, dual-setup journeys to `application/`, source
restoration to `sessions/`, and source HTTP requests to `transport/`. The large
feed collection is separated into frame/transport contracts, manager selection,
Shoonya authentication/history/lifecycle, and Yahoo lifecycle. Monitor cases are
separated into session journeys, event delivery, alert wiring, and HTTP transport.

The migration first passed all **274 existing Python cases with the same
assertions**. The package-import update from the preceding pass is the only
normalization needed to compare the original test method bodies. Afterward, one
adapter expectation changed to reflect the new
ownership boundary: adapters mark incoming history as live for every instrument;
the real monitor decides which primary confirmations may notify. A new test uses
the actual feed and monitor with a recording notifier to verify that comparison
history remains silent and a fresh primary confirmation recovers exactly once.

`application/test_transition_order.py` observes actual saved files and client
queues at publication. It distinguishes wall-clock closure from an admitted
quote's clock, checks simultaneous close batches before the new price appears,
and protects alert eligibility when a confirmation timestamp is still ahead of
the monitor's clock.
