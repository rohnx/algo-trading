# PCR / expiry feature: skill discovery

Researched on **2026-10-23**. Discovery only; no skill was installed. This folder preserves research and prompts; monitor behavior was not changed.

## Recommendation

**Use the installed `agent-skills` bundle. No additional finance or trading skill is justified for this feature.** Its existing requirements, source verification, implementation, UI, testing, and review workflows cover the engineering work. Market-specific decisions must come from the approved feature requirements and current Shoonya/NSE/BSE evidence.

Use the following sequence in separate planning and implementation conversations:

1. **Clarify and specify:** `interview-me` only while the PCR/risk definitions remain ambiguous; then `spec-driven-development` to record accepted definitions, data quality behavior, boundaries, and acceptance criteria. Do not start implementation in the discovery conversation.
2. **Implement and verify:** `source-driven-development` for version-specific broker/library behavior, `incremental-implementation` for small complete slices, `test-driven-development` for calculations and boundaries, and `frontend-ui-engineering` while building the graph, expiry badge, and risk table. Reuse the repository's existing browser-test runner.
3. **Review:** `code-review-and-quality` after implementation, emphasizing timestamp alignment, partial/missing OI, expiry selection and classification, risk-table boundaries, and existing ORB behavior.

For a short user-facing recommendation, list just the stage skills most relevant to the current conversation instead of invoking every skill at once. `api-and-interface-design` is useful when the PCR/expiry payload or provider boundary is actually designed; it does not need a separate installation or a prominent recommendation.

## Verified installed skills and adoption

Source for every row: **[addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)**, already installed locally from plugin version **0.6.13**. Public GitHub API returned **109,778 stars** during this research. This is an established engineering workflow source; popularity is evidence of adoption, not a substitute for reading its instructions.

Exact install counts below are from the public `skills.sh/api/search` response, retrieved at **2026-10-23T10:02:28Z**. Directory pages and CLI output display rounded and sometimes cached counts, so those may differ. Raw responses with query URLs are preserved in [skills-metadata.json](skills-metadata.json).

| Skill | Exact installs | Application here |
|---|---:|---|
| [interview-me](https://skills.sh/addyosmani/agent-skills/interview-me) | 55,351 | Resolve ambiguous PCR, ORB confirmation, expiry and risk definitions one decision at a time. |
| [spec-driven-development](https://skills.sh/addyosmani/agent-skills/spec-driven-development) | 62,844 | Save agreed behavior and acceptance criteria before coding. |
| [source-driven-development](https://skills.sh/addyosmani/agent-skills/source-driven-development) | 56,213 | Check current official API/library documentation and flag gaps or contradictions. |
| [api-and-interface-design](https://skills.sh/addyosmani/agent-skills/api-and-interface-design) | 60,325 | Define a payload that carries values, source times, selected universe, coverage and availability states. |
| [incremental-implementation](https://skills.sh/addyosmani/agent-skills/incremental-implementation) | 59,958 | Prove data collection first, then calculations, graph/badges and table, in small verified slices. |
| [test-driven-development](https://skills.sh/addyosmani/agent-skills/test-driven-development) | 59,870 | Test PCR zero/missing denominators, stale/partial chains, thresholds, expiry edge cases and 10:15 boundaries. |
| [frontend-ui-engineering](https://skills.sh/addyosmani/agent-skills/frontend-ui-engineering) | 65,243 | Fit the existing dashboard; show freshness/unavailable states, accessible labels and responsive layout. |
| [code-review-and-quality](https://skills.sh/addyosmani/agent-skills/code-review-and-quality) | 70,740 | Review behavior, contracts, scope, regressions and evidence after implementation. |

Installed actual `SKILL.md` contents were inspected at `/home/rohan/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/<skill>/SKILL.md`. The planning skill has explicit stop points after saving the spec and human review gates between phases. The interview skill is for an interactive clarification session; this research did not start that interview. `source-driven-development` prefers detected project versions and official deep links. The testing skill says to discover and reuse the repository's actual test commands.

No install command is needed because these skills are already available.

## Search performed

Read the user-invoked [find-skills instructions](/home/rohan/.agents/skills/find-skills/SKILL.md), then checked the [skills.sh leaderboard](https://skills.sh/) **before** running three targeted CLI searches:

```text
options open interest
financial data
playwright
```

Used an isolated temporary npm cache at `/tmp/orb-skills-cache`. Restricted-network attempts could not resolve the public npm registry; approved read-only public-directory queries then completed. These were `find` operations, never `add` operations.

## Candidates considered and excluded

| Candidate | Verified adoption/source | Reason excluded |
|---|---|---|
| [staskh/trading_skills: option-chain](https://skills.sh/staskh/trading_skills/option-chain) | CLI: **104 installs**; [GitHub](https://github.com/staskh/trading_skills): **386 stars**. Independent options-advisor project. | [Actual skill](https://raw.githubusercontent.com/staskh/trading_skills/main/.claude/skills/option-chain/SKILL.md) fetches Yahoo Finance chains with `yfinance`, demonstrates AAPL/SPY/TSLA, and mandates `America/New_York`. This does not solve the project's Shoonya NIFTY/SENSEX data and IST requirements. |
| [anthropics/financial-services: option-vol-analysis](https://skills.sh/anthropics/financial-services/option-vol-analysis) | CLI: approximately **2,000 installs**; [GitHub repository](https://github.com/anthropics/financial-services): **40,716 stars**. Partner-built LSEG skill hosted in Anthropic's repository. | [Actual skill](https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/partner-built/lseg/skills/option-vol-analysis/SKILL.md) covers volatility surfaces, Greeks and implied-versus-realized volatility through LSEG MCP tools. It neither implements PCR nor specifies Shoonya integration. |
| [anthropics/skills: webapp-testing](https://skills.sh/anthropics/skills/webapp-testing) | Directory: **185.7K installs**; [GitHub](https://github.com/anthropics/skills): about **183.1K stars**. Official Anthropic skill. | [Actual skill](https://raw.githubusercontent.com/anthropics/skills/main/skills/webapp-testing/SKILL.md) is useful Python Playwright guidance with server lifecycle helpers. The repository already has a Playwright runner, so adding another workflow is unnecessary here. |

The installed `browser-testing-with-devtools` skill explicitly requires a Chrome DevTools MCP server. That server is absent from the available tool inventory. Use the existing browser test setup rather than promising this skill's unavailable MCP capabilities.

## Definitions for the next planning conversation

Do not let a third-party trading prompt choose these product rules:

- OI PCR versus traded-volume PCR; which expiry or expiries; full-chain versus selected strikes; zero, missing, stale and incomplete data handling.
- PCR snapshot cadence and history horizon; freshness limit; minimum coverage; alignment of option snapshots to the ORB candle or event.
- Which ORB range and candle close confirm a break; strict `> 1` / `< 1`, neutral equality, and display-only confirmation versus alert gating.
- NIFTY/SENSEX weekly/monthly badge semantics, holiday adjustments, and cases where an expiry serves both weekly and monthly roles. Contract-date discovery alone does not prove the expiry type.
- What `R` means, which range determines it, exact factor values, before/at/after **10:15 Asia/Kolkata** inclusivity, and whether the risk table is explanatory or changes sizing.

The completed provider probes are separate evidence. A successful OI quote or history response does not by itself establish complete, synchronized PCR for an agreed universe.
