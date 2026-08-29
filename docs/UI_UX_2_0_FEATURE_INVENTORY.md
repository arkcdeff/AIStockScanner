# UI/UX 2.0 Feature Inventory

**Sprint:** 20.0  
**Date:** 2026-08-22  
**Mode:** Audit, design, and documentation only  
**Production code changed:** No

> Post-inventory update (2026-08-24): Productization Stage 1 has implemented the shared shell,
> dashboard, Data Health, stock explorer/detail, scanner journey, and historical Derivatives
> Workbench findings from this baseline. The table below remains the pre-implementation inventory;
> see `05_Development/Sprint_20.md` for verified current status.

## 1. Purpose

This report maps the implemented product surface before UI/UX 2.0 work begins. It distinguishes backend capability, public API, browser route, template coverage, navigation exposure, and remaining productization work.

Status terms:

- **Visible:** reachable through normal navigation and represented by a complete page.
- **Nested:** reachable from another page but not represented in primary navigation.
- **Hidden:** implemented as an API, service, or HTMX partial without a reliable user entry point.
- **Foundation only:** persistence or service capability intentionally has no user workflow.
- **Mismatch:** navigation promises a capability but opens a different or generic surface.

## 2. Platform Inventory

| Domain | Implemented capability | Primary code | Browser/UI status | Priority |
|---|---|---|---|---|
| Authentication | Login/logout, profile, password, preferences, session revocation | `auth.py`, `auth_pages.py`, `auth_service.py`, `auth/*` | Visible through user menu; sessions/settings are nested | P1 |
| Authorization | Users, roles, permissions, assignments, audit-aware enforcement | `admin.py`, `admin_pages.py`, `role_service.py`, `user_admin.py` | Users/roles visible to admins; permissions page nested/omitted | P1 |
| Dashboard | Market/data/scanner/portfolio/alert/AI summaries and search | `dashboard.py`, `dashboard.py`, `dashboard.html` | Visible but overloaded | P0 |
| Equity market data | Import, download, backfill, prices, coverage, readiness, import history | `market_data.py`, `market_data_pages.py`, `market_data_service.py` | Visible but overloaded | P0 |
| Indicators | Calculation, status, stock indicator cards | `indicators.py`, `indicator_pages.py`, `indicator_engine.py` | Complete page, absent from sidebar | P1 |
| Scanners | Definition library, execution, results, filters, exports | `scanners.py`, scanner page routes, `scanner_engine.py`, `scanner_workspace.py` | Library visible; run/results and exports poorly exposed | P0 |
| Equity analytics | Scores, top rated, trending, breakouts, symbol detail | `analytics.py`, `analytics_pages.py`, `analytics_engine.py` | Visible; generic label and limited workflow | P1 |
| AI explanations | Per-stock explanation, collections, market summary | `ai.py`, `ai_pages.py`, AI services | Visible | P2 |
| Watchlists | CRUD, default list, membership, notes, latest prices | watchlist routes/services/templates | Visible | P1 |
| Portfolio | Portfolio/holding CRUD, valuation, P&L, allocation | portfolio routes/service/templates | Visible | P1 |
| Alerts | Rules, execution, events, scopes and read state | alert routes/service/templates | Visible | P1 |
| Notifications | Channels, preferences, providers, history, retries, summaries | notification routes/services/templates | Main page visible; settings/history nested | P1 |
| Pipeline | EOD run, status, stage results and history | pipeline routes, `eod_pipeline.py`, `pipeline.html` | Visible | P1 |
| Scheduler | EOD and notification scheduler status/manual actions | `scheduler_pages.py`, scheduler services, `scheduler.html` | Complete page, absent from sidebar | P1 |
| Backtesting | Strategies, runs, simulator, metrics, equity, trades | backtest routes/services/templates | Visible; strategies/history nested | P1 |
| Market intelligence | Composite regime, indices, global, institutional, breadth, sectors, macro, GIFT Nifty | market-intelligence routes and intelligence services | Visible but long and dense | P0 |
| Historical futures | Contract curve, OI buildup, basis, rollover | market-data routes, `historical_futures_analytics.py` | Nested in Market Data | P0 |
| Historical options | Chain reconstruction, expiries, PCR, max pain, OI walls | market-data routes, historical option services | Nested in Market Data | P0 |
| Participant positioning | Participant OI/volume and FII derivatives | market-data routes, participant services | Nested in Market Data | P1 |
| Advanced options | IV, Greeks, expected move, skew, term structure | `advanced_options_service.py`, advanced options partial | Hidden partial | P0 |
| Exposure analytics | Estimated GEX/DEX, gamma flip and strike exposure | `advanced_exposure_service.py`, advanced exposure partial | Hidden partial; sidebar points elsewhere | P0 |
| Options opportunities | Expiry/moneyness/strike selection and ranking | options opportunity/ranking services, opportunity partials | Hidden partial | P0 |
| Stock360 research | Consolidated research and opportunity intelligence | `stock360_service.py`, `stock360_cards.html` | Hidden partial; sidebar label is misleading | P0 |
| Fundamentals/corporate | Fundamentals and corporate intelligence | `fundamental_corporate_service.py`, `fundamentals_cards.html` | Hidden partial | P0 |
| News/policy | News and government policy intelligence | `news_policy_service.py`, `news_cards.html` | Hidden partial | P1 |
| Historical validation | Walk-forward validation and coverage certification | historical validation services, `validation_lab_tab.html` | Hidden partial; confused with backtesting | P0 |
| Trading foundation | Paper accounts, orders, executions, positions | trading models/repositories/service | Foundation only by explicit Sprint 19 scope | P2 |

## 3. Browser Page Route Inventory

All routes require authenticated browser access unless marked public. Permissions are the explicit route-level requirements observed during the audit.

| Method | Route | Module | Template/purpose | Access |
|---|---|---|---|---|
| GET/POST | `/login` | `auth_pages.py` | `auth/login.html` | Public |
| GET | `/profile` | `auth_pages.py` | Account profile | Authenticated |
| GET | `/settings` | `auth_pages.py` | User preferences | Authenticated |
| GET | `/sessions` | `auth_pages.py` | Active sessions | Authenticated |
| GET | `/`, `/dashboard` | `dashboard.py` | `dashboard.html` | `dashboard.read` |
| GET | `/stocks`, `/stocks/` | `stocks_pages.py` | Equity browser | `stocks.read` |
| GET | `/stocks/{symbol}` | `stock_detail.py` | Stock workspace | `stocks.read` |
| GET | `/analytics`, `/analytics/` | `analytics_pages.py` | Equity rankings | `analytics.read` |
| GET | `/ai`, `/ai/` | `ai_pages.py` | AI explanations | `ai.read` |
| GET | `/indicators`, `/indicators/` | `indicator_pages.py` | Indicator operations/status | `stocks.read`; calculation uses `market_data.write` |
| GET | `/watchlists`, `/watchlists/` | `watchlist_pages.py` | Watchlist workspace | `watchlists.read` |
| GET | `/portfolio`, `/portfolio/` | `portfolio_pages.py` | Portfolio workspace | `portfolio.read` |
| GET | `/alerts`, `/alerts/` | `alert_pages.py` | Alert rules/events | `alerts.read` |
| GET | `/notifications`, `/notifications/` | `notification_pages.py` | Notification center | `notifications.read` |
| GET | `/notifications/settings` | `notification_pages.py` | Channels/preferences/summaries | `notifications.read` |
| GET | `/notifications/history` | `notification_pages.py` | Delivery history | `notifications.read` |
| GET | `/market-data`, `/market-data/` | `market_data_pages.py` | Data operations and derivatives tabs | `stocks.read` |
| GET | `/market-intelligence`, `/market-intelligence/` | `market_intelligence_pages.py` | Composite market workspace | `market_intelligence.read` |
| GET | `/scanner-library`, `/scanner-library/` | `scanner_library_pages.py` | Scanner catalog | `scanners.read` |
| GET | `/scanner-run`, `/scanner-run/` | `scanner_run_pages.py` | Scanner execution | `scanners.read`; execution uses `scanners.run` |
| GET | `/scanner-results/` | `scanner_workspace.py` | Results/filter/export workspace | `scanners.read` |
| GET | `/pipeline`, `/pipeline/` | `pipeline_pages.py` | EOD pipeline | `stocks.read`; execution uses `pipeline.run` |
| GET | `/scheduler`, `/scheduler/` | `scheduler_pages.py` | Scheduler operations | `stocks.read`; actions use `scheduler.run` |
| GET | `/backtests`, `/backtests/` | `backtest_pages.py` | Backtest workspace | `backtests.read` |
| GET | `/backtests/strategies` | `backtest_pages.py` | Strategy management | `backtests.read` |
| GET | `/backtests/runs` | `backtest_pages.py` | Run history | `backtests.read`, owner/admin scope |
| GET | `/backtests/runs/{run_id}` | `backtest_pages.py` | Run detail | `backtests.read`, owner/admin scope |
| GET | `/admin/users` | `admin_pages.py` | User management | Browser admin |
| GET | `/admin/users/{user_id}` | `admin_pages.py` | User detail | Browser admin |
| GET | `/admin/roles` | `admin_pages.py` | Role management | Browser admin |
| GET | `/admin/roles/{role_id}` | `admin_pages.py` | Role detail | Browser admin |
| GET | `/admin/permissions` | `admin_pages.py` | Permission catalog | Browser admin |

The application also contains numerous POST mutation routes and HTMX fragments for forms, tables, cards, polling, and detail panels. These are supporting interactions, not primary navigation destinations.

## 4. API Capability Groups

The principal REST groups under `/api/v1` are:

| Prefix | Capability |
|---|---|
| `/auth` | Tokens, current user, profile, preferences, password and sessions |
| `/admin` | Users, roles, permissions, assignments and RBAC seed |
| `/stocks` | Stock catalog, detail and price data |
| `/market-data` | Imports, readiness, coverage, historical equity/derivatives/fundamental data |
| `/indicators` | Calculation and status |
| `/scanners` | Definitions, execution and results |
| `/pipeline` | EOD execution, status and run history |
| `/analytics` | Symbol analytics, top-rated, trending and breakouts |
| `/ai` | Stock explanations and market summaries |
| `/watchlists` | Watchlist and membership CRUD |
| `/portfolios` | Portfolio, holdings and valuation |
| `/alerts` | Rules, runs, events and read state |
| `/notifications` | Status, channels, preferences, delivery history/retry and summaries |
| `/backtests` | Strategies, execution, metrics, equity and trades |
| `/market-intelligence` | Composite and component intelligence snapshots |
| `/health` | Application and database health |

## 5. Template and Asset Inventory

### Page templates

- Shell/account: `base.html`, `layouts/shell.html`, `auth/*`.
- Core workspaces: `dashboard.html`, `stocks.html`, `stock_detail.html`, `analytics.html`, `ai.html`.
- Operations: `market_data.html`, `pipeline.html`, `scheduler.html`, `indicators.html`.
- Research: `market_intelligence.html`, scanner templates, `backtests/*`.
- User workflows: `watchlists.html`, `portfolio.html`, `alerts.html`, `notifications/*`.
- Administration: `admin/*`.

### High-value reusable components

- Derivatives: `option_chain_table.html`, `futures_analytics_table.html`, `options_analytics_cards.html`, `participant_positioning_cards.html`, `advanced_options_tab.html`, `advanced_exposure_tab.html`, `options_opportunity_tab.html`, `derivatives_replay_cards.html`.
- Research: `stock360_cards.html`, `fundamentals_cards.html`, `news_cards.html`, `opportunity_board.html`, `validation_lab_tab.html`.
- Stock: overview, analytics, AI, indicators, scanner matches, portfolio, watchlist, alerts and notifications panels.
- Platform: sidebar, topbar, flash, status cards, import/coverage tables, scheduler and pipeline cards.

### Static assets

- Design shell: `design-system.css`, `dashboard.css`, `shell.js`, `dashboard.js`.
- Specialized pages: `stock_detail.css`, `backtests.css`, `backtests.js`, `notifications.css`, `notifications.js`, `auth.css`.

## 6. Navigation Audit

### Current strengths

- Navigation is grouped into Main, Market, Stocks, Derivatives, Research & Validation, Portfolio, Monitoring, System and Admin.
- The responsive shell, off-canvas mobile navigation, active states, user footer and theme controls already exist.
- Major operational domains are represented at least once.

### Critical mismatches

| Current item | Problem | Required correction |
|---|---|---|
| Data Coverage | Links to `/market-data/coverage`, an HTMX fragment without the shell | Link to a full Data Health page or a supported Market Data deep link |
| Futures & Option Chain | Links to generic `/market-data`, opening the default import tab | Create a dedicated derivatives workspace/deep link |
| PCR, GEX & Options Engine | Links to `/analytics`, which contains equity rankings | Link to the options analytics workspace |
| Stock Analysis & 360 degrees | Links to stock list; Stock360 cards are unattached elsewhere | Make stock search a launcher into a real Stock360 workspace |
| Backtesting & Validation Lab | Links only to backtests; historical validation is a separate hidden partial | Separate Backtesting from Historical Validation |

Duplicate destinations also cause two sidebar items to appear active at once for `/market-data` and `/analytics`.

### Missing navigation

- Scanner Run and Scanner Results.
- Indicators and Scheduler.
- Notification Settings and History.
- Backtest Strategies and Run History.
- Admin Permissions.
- Advanced Options, Exposure/GEX, Options Opportunities, Validation Lab, Fundamentals, News and Stock360.

Navigation should remain role-aware. Non-admin links currently are not consistently filtered by permission, so users can see destinations that return 403.

## 7. Workflow Audit

| User goal | Current path | Assessment |
|---|---|---|
| Understand today's market | Dashboard -> Market Intelligence | Available, but duplicated and vertically dense |
| Verify data freshness | Dashboard -> Market Data -> Coverage tab | Available; sidebar coverage link is broken as a full page |
| Import/backfill NSE data | Market Data forms | Functional and appropriately operational |
| Analyze a stock | Stocks -> stock detail tabs | Functional; lacks a coherent Stock360 narrative |
| Run a scanner | Scanner Library -> Scanner Run | Available; query parameter mismatch can prevent preselection |
| Review scanner results | Know `/scanner-results/` or navigate indirectly | Poor discoverability; filters/exports are not properly surfaced |
| Analyze futures/options | Market Data -> multiple tabs | Functional but buried among ingestion operations |
| Find options opportunities | No normal entry point | Backend/partial exists; hidden |
| Review fundamentals/news | No normal entry point | Backend/partials exist; hidden |
| Validate research historically | No normal entry point | Validation partial exists; hidden and conflated with backtesting |
| Create and inspect backtests | Backtests -> strategies/runs | Functional; secondary pages are nested |
| Manage alerts/notifications | Alerts and Notifications | Functional; settings/history are nested |
| Operate EOD automation | Pipeline plus unlinked Scheduler | Split and partially hidden |
| Manage access | Admin Users/Roles; permissions by local link | Functional; permission catalog missing from sidebar |

## 8. Highest-Priority Findings

### P0

1. Establish truthful navigation destinations for Data Health, Derivatives, Options Analytics, Stock360 and Validation.
2. Split ingestion operations from research analytics currently concentrated in `/market-data`.
3. Expose advanced options, exposure/GEX, options opportunities, fundamentals, news and validation through complete pages.
4. Repair the scanner journey from library to run to filtered results and exports.
5. Reduce dashboard scope to decision-oriented summaries and clear drill-downs.

### P1

1. Add Indicators, Scheduler, notification subpages, backtest subpages and Admin Permissions to contextual navigation.
2. Add permission-aware navigation and align page-read permissions with domain semantics.
3. Break Market Intelligence into anchored sections or focused subordinate views.
4. Make stock detail a coherent research workspace with progressive disclosure.

### P2

1. Consolidate design tokens and remove obsolete layout duplication.
2. Standardize empty/loading/error/freshness/provenance states.
3. Defer paper-trading UI until its domain service is intentionally productized.

## 9. Audit Conclusion

The product is not feature-poor; it is feature-obscured. The backend and component inventory supports a professional research platform, but the current information architecture is organized around implementation history and generic pages. UI/UX 2.0 should first make existing capabilities discoverable and correctly grouped, then refine visual presentation without changing deterministic analytics or data contracts.
