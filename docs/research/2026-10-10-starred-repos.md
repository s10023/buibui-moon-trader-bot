# Starred repos: a catalogue with one verdict each (issue #1009)

Date: 2026-10-10. Source: `gh api --paginate user/starred` for `s10023`, 58 repos. Trading,
memory, ingest and FPL/finance READMEs were read this run (a sonnet agent, README only, no
code run). The rest are judged from their GitHub descriptions. Health filter as in the
2026-08-14 canon audit: archived, or no push in 12 months, means skip. One star failed it.
None is archived.

## Verdict

The stars hold no new integrate or implement work for buibui. Four are already integrated
(`superpowers`, `mattpocock/skills`, `humanizer`, `book-to-skill`), and the trading-agent
frameworks are LLMs picking trades with no out-of-sample evidence or price-only re-slices,
which is the class this repo has already ruled out. Three explore leads are owned by open
Issues rather than new ones: Polymarket and StockTwits sentiment through `last30days-skill`
(#1010), local scanned-PDF triage through `pdf-inspector` (#803), and the scheduled-push
pattern in `daily_stock_analysis` (#1012). The actionable verdicts for the sibling repos
belong to those repos' sessions, which pull their rows from the table below:
`FPL-Core-Insights` (integrate) and `open-fpl-solver` (implement) for fpl, and `wealthfolio`
(explore) for fund-management.

## How to read the table

- **Verdict**: integrate = a dependency, plugin or MCP to wire in · implement = copy an idea
  into our own code · explore = read against a named file or Issue · skip. "done" marks a
  star already integrated.
- **Repo**: which of our repos the row serves. `buibui` = this repo, `wifey` = the equities
  fork, `fpl`, and `fund-management` (private; keep it local-only, so a cloud-synced or
  hosted tool is out).
- **New data?** is recorded where it decides the verdict. The binding constraint (AGENTS.md)
  is new information, so a tool that re-slices price or text we already hold cannot be the
  next edge.

## Catalogue

### Research, backtest and trading agents

| Repo | Verdict | Repo for | Reason |
| --- | --- | --- | --- |
| TauricResearch/TradingAgents | skip (buibui) · explore (wifey) | buibui, wifey | Multi-agent LLM trade picker, no OOS evidence; the card already carries a four-angle steelman. For wifey, its as-filed SEC EDGAR contract is worth reading against the fork's fundamentals loader |
| shiyu-coder/Kronos | skip | buibui, wifey | Already rated in the 2026-08-14 canon audit (a model, not data). It forecasts from OHLCV alone, so it re-slices price |
| virattt/ai-hedge-fund | skip | — | Educational persona agents on a paid data vendor, with no OOS evidence |
| HKUDS/Vibe-Trading | skip | — | LLM strategy generation on public feeds we already pull; README warns of impersonating tokens |
| The-Swarm-Corporation/AutoHedge | skip | — | Live Solana trading from a raw wallet key in env, with LLM-picked trades |
| ZhuLinsen/daily_stock_analysis | explore | buibui, wifey | Read its Actions-scheduled Telegram digest against #1012's "nothing schedules the brief". Its LLM buy/sell points are not the reason |
| xbtlin/ai-berkshire | skip (buibui) · explore (wifey) | wifey | Value-investing skill prompts; its return claims are an unverifiable screenshot. Possible prompt ideas for equity research only |
| paperswithbacktest/awesome-systematic-trading | skip | — | Already rated in the canon audit (stale list, still an index); reference only |

### Data, feeds and ingest

| Repo | Verdict | Repo for | Reason |
| --- | --- | --- | --- |
| mvanhorn/last30days-skill | explore | buibui, wifey | The one star that carries new information (Polymarket odds, StockTwits sentiment, engagement counts). Belongs in #1010's survey, and any card or brief input needs a power-priced test before it is wired in. X/TikTok legs need logins |
| Panniantong/Agent-Reach | skip | buibui | Social read access via exported login cookies (ToS and ban risk); `/ingest-x` already runs at about $0 on Apify |
| bradautomates/claude-video | skip | buibui | Rejected by the 2026-07-28 ingest-video design (scene-change frames, full frame set to main thread); its edge cases were cribbed |
| firecrawl/pdf-inspector | explore | buibui, fund-management | Local text-vs-scan classifier, relevant to #803's shelf, where one book is a photocopier scan and `pdfinfo` was the test. Read before the next book ingest |
| KnockOutEZ/wigolo | skip | — | Beta search MCP, unclear licence (AGPL badge vs NOASSERTION), 1.5 GB download; ingest needs none of it |
| public-apis/public-apis | skip | — | Index only; `2026-10-07-data-sources.md` is the data-source register |
| NanmiCoder/MediaCrawler | skip | — | Chinese social-platform scraper; ToS risk, and zh pundits are ingested from YouTube |
| cv-cat/Spider_XHS | skip | — | Xiaohongshu reverse-engineering scraper; same reason |
| microsoft/playwright-mcp | skip | buibui | Browser MCP; `agent-browser` already backs the Coinglass capture and the built-in browser covers the rest |

### Claude tooling: skills, plugins, MCP, memory

| Repo | Verdict | Repo for | Reason |
| --- | --- | --- | --- |
| obra/superpowers | done | buibui | Enabled in `.claude/settings.json` |
| mattpocock/skills | done | all | Installed account-wide; the triage, spec and review flow |
| blader/humanizer | done | buibui | Vendored under `.agents/skills/humanizer` |
| virgiliojr94/book-to-skill | done | buibui | The book-distil tool `/research-distil` reads by path |
| vectorize-io/hindsight | skip | — | Agent memory server; overlaps the memory tree, and its cloud option sends memory off-machine |
| akitaonrails/ai-memory | skip | — | Hooks capture every prompt and tool call into a server; conflicts with the handoff and MEMORY.md protocol |
| DeusData/codebase-memory-mcp | skip | — | Third-party binary that rewrites client configs across 45 surfaces; Grep and Explore already cover navigation |
| addyosmani/agent-skills | skip | — | Spec-to-ship skills that overlap mattpocock-skills |
| ComposioHQ/awesome-claude-skills | skip | — | A list; #1010 surveys marketplaces properly |
| google/skills | skip | — | Google-product skills |
| anthropics/claude-code | skip | — | The harness itself; reference |
| anthropics/claude-cookbooks | skip | — | API recipes; reference |
| openai/codex-plugin-cc | skip | — | Codex second opinion; review runs through the two code-review skills |
| ayghri/i-have-adhd | skip | — | Output-style skill; the account CLAUDE.md sets output shape |
| petergyang/no-ai-slop | skip | — | Same job as the vendored `humanizer` |
| tt-a1i/archify | skip | — | Diagram skill; artifacts cover diagrams |
| cathrynlavery/diagram-design | skip | — | Same |
| hugohe3/ppt-master | skip | — | Slide decks; unrelated |
| decolua/9router | skip | — | Free-LLM router; routes code through unvetted third-party providers |
| tashfeenahmed/freellmapi | skip | — | Same |
| CodebuffAI/freebuff | skip | — | Alternative coding agent |

### Personal finance (fund-management)

None serves buibui. The column for fund-management is a lead for that repo's session, and its
data stays local: a hosted sync tier or cloud backend rules a tool out there.

| Repo | Verdict | Repo for | Reason |
| --- | --- | --- | --- |
| wealthfolio/wealthfolio | explore | fund-management | Local-first portfolio tracker with CSV import; the closest fit. Skip its paid brokerage-sync tier |
| actualbudget/actual | explore | fund-management | Local-first budgeting |
| firefly-iii/firefly-iii | skip | fund-management | Self-hosted server; heavier than a local repo needs |
| cioraneanu/firefly-pico | skip | fund-management | A Firefly III front end |
| we-promise/sure | skip | fund-management | Hosted-first app |
| mayswind/ezbookkeeping | skip | fund-management | Self-hosted server bookkeeping |
| ananthakumaran/paisa | explore | fund-management | Plain-text ledger based, which suits a git-tracked private repo |
| securo-finance/securo | skip | fund-management | Self-hosted server |
| whisper-money/whisper-money | skip | fund-management | Hosted app |
| ellite/Wallos | skip | fund-management | Subscription tracker |
| jakubgarfield/expenses | skip | — | Fails the health filter (last push 2024-02) |

### FPL

| Repo | Verdict | Repo for | Reason |
| --- | --- | --- | --- |
| olbauday/FPL-Core-Insights | integrate | fpl | New data: per-match stats and ClubElo joined by FPL id, refreshed twice daily, 2026/27 included. Check reuse terms first: no licence file |
| solioanalytics/open-fpl-solver | implement | fpl | HiGHS squad and transfer optimiser fed from a projections CSV; its default projections are a paid export |

### Unrelated

| Repo | Verdict | Repo for | Reason |
| --- | --- | --- | --- |
| bilawalsidhu/gods-eye-view | skip | — | 3D satellite viewer |
| BraveOPotato/NoSignups | skip | — | Tool list |
| pranshuparmar/witr | skip | — | Process tracer CLI |
| agavra/tuicr | skip | — | Code-review TUI |
| BetaStreetOmnis/xhs_ai_publisher | skip | — | Xiaohongshu publishing |
| career-ops-hq/career-ops | skip | — | Job search agent |
| iptv-org/iptv | skip | — | IPTV list |

## Ownership

No row creates new buibui work, so this doc files no Issue of its own. The three explore
leads are recorded as comments on #803, #1010 and #1012, each naming this file. The fpl and
fund-management rows are for those repos' sessions to pick up.
