# UI/UX 2.0 Implementation Plan

**Sprint:** 20.0 design output  
**Date:** 2026-08-22  
**Current mode:** Documentation only  
**Implementation authorization:** Not granted by this report

## 1. Delivery Strategy

Implement UI/UX 2.0 as incremental, testable slices. Reuse existing services, APIs, templates and partials. Do not change deterministic analytics, scores, data models or migrations merely to reorganize presentation.

Priorities:

- **P0:** truthful navigation, hidden research surfaces, critical workflows and overloaded pages.
- **P1:** contextual navigation, consistency, permissions and operational usability.
- **P2:** cleanup, refinement and intentionally deferred foundations.

## 2. Phase Plan

The plan contains 12 phases and 38 implementation stages.

### Phase 20.1: Baseline and Contracts

1. **20.1.1 Inventory lock:** Validate the route/template/API map against the implementation branch and record approved canonical URLs.
2. **20.1.2 UX acceptance matrix:** Convert page specifications into desktop/tablet/mobile, accessibility, RBAC and state acceptance checks.
3. **20.1.3 Browser baseline:** Capture authenticated screenshots and browser checks for current primary pages before changing behavior.

**Exit:** Approved route map and measurable baseline; no business-logic changes.

### Phase 20.2: Design System and Shell

4. **20.2.1 Token audit:** Consolidate typography, spacing, color, status, elevation and table/form tokens in the existing design system.
5. **20.2.2 State components:** Standardize loading, empty, stale, unavailable, error, permission and freshness/provenance states.
6. **20.2.3 Shell accessibility:** Verify focus order, skip link, landmarks, tooltips, off-canvas controls and keyboard behavior.
7. **20.2.4 Responsive shell:** Certify sidebar/topbar/content behavior at mobile, tablet and desktop breakpoints.

**Exit:** Shared shell and components can support all later workspaces.

### Phase 20.3: Navigation and Routing

8. **20.3.1 Permission-aware navigation:** Match item visibility to route permissions and the canonical admin predicate.
9. **20.3.2 Correct misleading links:** Remove fragment destinations and duplicate generic links from the primary sidebar.
10. **20.3.3 Workspace local navigation:** Add reusable local tabs/section navigation for Derivatives, Research, Notifications, Backtesting and Admin.
11. **20.3.4 Compatibility decision:** Preserve or redirect old routes only where tests, bookmarks or consumers establish a concrete need.

**Exit:** Every visible navigation item opens a complete and correctly labeled destination.

### Phase 20.4: Dashboard and Market Overview

12. **20.4.1 Dashboard hierarchy:** Reduce the page to market state, priority opportunities/alerts, portfolio impact and operational exceptions.
13. **20.4.2 Drill-down wiring:** Ensure every dashboard card links to the canonical filtered workspace.
14. **20.4.3 Truthful status:** Standardize market date, source, freshness, replay/live and pipeline badges.

**Exit:** Dashboard answers immediate decision questions without duplicating entire workspaces.

### Phase 20.5: Data and Operations

15. **20.5.1 Data Workspace:** Retain upload/download/backfill/import history in a focused complete page.
16. **20.5.2 Coverage & Quality:** Wrap coverage, readiness, issues and certification in a full shell page.
17. **20.5.3 Pipeline refinement:** Improve stage visibility, failure navigation and run history.
18. **20.5.4 Scheduler exposure:** Add the existing scheduler page to permission-aware operations navigation.
19. **20.5.5 Indicators exposure:** Add calculation/status workflow with clear write-permission behavior.

**Exit:** Operators can import, diagnose and automate data without entering research workspaces.

### Phase 20.6: Scanner Journey

20. **20.6.1 Scanner library:** Correct run parameter wiring and clarify prerequisites/categories.
21. **20.6.2 Scanner run:** Provide date/universe selection, readiness and deterministic execution feedback.
22. **20.6.3 Results workspace:** Expose all implemented filters, sorting, pagination and stable HTMX targets.
23. **20.6.4 Export and drill-down:** Surface CSV/Excel/PDF controls and Stock360 links with preserved context.

**Exit:** Library -> Run -> Results -> Stock research works without hidden URLs.

### Phase 20.7: Stock360

24. **20.7.1 Stock header and summary:** Build a canonical symbol context and synthesized overview.
25. **20.7.2 Research tabs:** Integrate technicals, fundamentals, news/policy and derivatives using existing services/components.
26. **20.7.3 Monitoring tabs:** Reconcile watchlist, portfolio, alerts and notifications actions.
27. **20.7.4 Permission and performance:** Lazy-load heavy panels and apply matching permissions to full and partial renders.

**Exit:** The sidebar's Stock360 promise is backed by a coherent, complete workflow.

### Phase 20.8: Derivatives Workspace

28. **20.8.1 Derivatives shell:** Create persistent underlying/date/expiry/source/coverage context.
29. **20.8.2 Futures and chain:** Move existing futures analytics and option chain from Market Data into dedicated tabs.
30. **20.8.3 Options analytics and exposure:** Expose PCR/max pain/walls, advanced options and estimated GEX/DEX/gamma flip.
31. **20.8.4 Opportunities and positioning:** Expose options ranking and participant/FII positioning with truthful no-trade states.

**Exit:** All implemented derivatives research is discoverable from one canonical workspace.

### Phase 20.9: Market Intelligence and Opportunities

32. **20.9.1 Intelligence hierarchy:** Add section navigation, factor details and provenance/freshness presentation.
33. **20.9.2 Opportunity Board:** Productize the existing board as a complete page with equity/derivative separation.
34. **20.9.3 Cross-domain context:** Link intelligence and opportunities to Stock360, Derivatives and Validation.

**Exit:** Market context and actionable candidates are connected without conflating score semantics.

### Phase 20.10: Research Validation and Backtesting

35. **20.10.1 Validation Lab:** Productize historical/walk-forward validation and coverage certification independently of backtests.
36. **20.10.2 Backtest navigation:** Clarify workspace, strategies, history and run detail; retain owner/admin enforcement.

**Exit:** Users can distinguish research validation from simulated strategy execution.

### Phase 20.11: Portfolio, Monitoring and Administration

37. **20.11.1 Secondary workflow alignment:** Standardize Portfolio, Alerts, Notifications, Account and Admin local navigation, states and permissions.

**Exit:** Supporting workflows use the same shell, state and RBAC contracts.

### Phase 20.12: Certification and Release Readiness

38. **20.12.1 Full certification:** Run focused and full tests, route/dead-link checks, accessibility review, Playwright desktop/tablet/mobile certification, Ruff, import, Alembic and `git diff --check`; update handoff/release documentation.

**Exit:** No regressions, no misleading links, no page-wide overflow, and documented evidence for every acceptance criterion.

## 3. Priority Backlog

### P0

- Correct Data Coverage, Derivatives, Options Engine, Stock360 and Validation navigation.
- Productize hidden full-page wrappers for advanced options, exposure/GEX, options opportunities, fundamentals/news, Stock360 and validation.
- Split Market Data operations from derivatives research.
- Repair Scanner Library -> Run -> Results and expose filters/exports.
- Simplify Dashboard and restructure Market Intelligence.

### P1

- Permission-aware sidebar and domain-consistent read permissions.
- Expose Scheduler, Indicators, Admin Permissions and secondary Notification/Backtest pages.
- Standardize freshness/provenance, empty/error/loading states and contextual deep links.
- Improve responsive tables and keyboard accessibility.

### P2

- Remove obsolete layout duplication after usage evidence.
- Refine visual density and chart accessibility.
- Keep trading foundation without a UI until a separate product scope authorizes paper-trading workflows.

## 4. Technical Guardrails

1. Prefer full-page wrappers around existing partials before rewriting services.
2. Do not duplicate scoring or analytical calculations in templates or JavaScript.
3. Keep HTMX endpoints partial-only and primary navigation endpoints full-page.
4. Preserve CSRF on every browser mutation.
5. Keep API and browser RBAC behavior aligned.
6. Do not fabricate data, freshness, live status or confidence.
7. Avoid model/migration changes unless a later stage proves a product requirement cannot be met with existing data.
8. Maintain owner/admin scoping for backtests and other private records.

## 5. Test Strategy

### Per-stage checks

- Route status and permission tests.
- Template context and XSS escaping tests.
- HTMX target/swap tests.
- Empty, stale, partial, unavailable and error-state tests.
- Mobile overflow checks for tables and workspaces.

### Phase gates

- Focused unit/UI tests for changed domains.
- Dead-link and full-page-vs-partial navigation checks.
- Browser console, page error, 404, 500 and HTMX failure capture.
- Role matrix checks for viewer, analyst, admin and super-admin behavior.

### Final gate

- Full pytest suite.
- Ruff.
- App import.
- Alembic current/heads/check.
- `git diff --check`.
- Real browser login/navigation and responsive screenshots.

## 6. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Route churn breaks tests/bookmarks | Introduce canonical pages incrementally; retain proven compatibility paths temporarily |
| Hidden partials lack full context | Define full-page context contracts and add route tests before linking them |
| Permission leakage on eager stock tabs | Apply the same permission checks to full render and partial endpoints |
| Dashboard/Stock360 query load | Lazy-load heavy sections and measure query counts |
| Derivatives tables overflow mobile | Contained scrolling or alternate strike-card presentation |
| Visual labels overstate data quality | Mandatory source/freshness/replay/live and completeness components |
| Scope expands into analytics changes | Treat existing service output as immutable unless separately authorized |

## 7. Definition of Done

- Four Sprint 20.0 design documents are approved.
- Each implementation stage has tests and evidence.
- Every primary navigation item is truthful, permission-aware and responsive.
- P0 hidden capabilities have complete user-facing workspaces.
- Generic overloaded pages have been separated by user intent.
- Existing analytics, models, migrations, APIs and data remain behaviorally stable unless a separately approved task changes them.
- Handoff and project-state documents identify completed stages and exact verification results.
