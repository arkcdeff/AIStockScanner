# AI HANDOFF — G.11-D-4 TARGETED REMEDIATION

DATE: 2026-09-18
BRANCH: checkpoint/sprint19-20-20260822
CERTIFIED BASE: 1cab27cb8ea8bf055b7b63dd069c14baced18933
ALEMBIC HEAD: f1b9c8d7e6a5 (verified via `alembic current` → `f1b9c8d7e6a5 (head)`)

TASK: G.11-D-4 remediation (3 independent-QA findings). Do NOT restart D-4.
Do NOT redesign architecture. Do NOT modify D-1/D-2/D-3. DO NOT COMMIT. DO NOT PUSH.

## FINDING 01 (P2) — HTMX / JSON API MISMATCH
STATUS: FIXED
FILES:
  - app/templates/components/what_changed_screener.html
      * hx-post changed /api/v1/stocks/screening/what-changed → /stocks/screening/what-changed
      * removed hx-ext="json-enc" (extension was never loaded in the project)
      * results block extracted to the results partial below (include)
  - app/templates/components/what_changed_screening_results.html (NEW — HTML fragment
    rendered by the UI route; supports flash error rendering via components/flash.html)
  - app/templates/what_changed_screening.html (NEW — shell page hosting the screener)
  - app/api/routes/stocks_pages.py (M)
      * GET  /stocks/screening/what-changed → renders the screener page
      * POST /stocks/screening/what-changed → HTML route: CSRF-verified, parses form →
        ScreeningRequest (presentation parsing only), calls WhatChangedScreeningService
        (no business-logic duplication), renders the results partial (HTMLResponse)
      * GET /stocks page now stages csrf_token (finalize_page_response) so the shell's
        htmx x-csrf-token auto-header works for the form
ARCHITECTURE (repo-consistent): UI → HTML-rendering route → WhatChangedScreeningService
→ HTML partial; REST API /api/v1/stocks/screening/what-changed remains pure JSON.
TESTS: tests/test_what_changed_screening_g11d4.py::TestScreeningHtmlRoutes
  - test_screener_page_renders_htmx_form (200 HTML, correct hx-post, no json-enc, CSRF staged)
  - test_htmx_post_returns_html_fragment (service executes, HTML fragment returned,
    form not duplicated, not JSON)
  - test_htmx_post_parses_form_parameters_into_request (form params accepted correctly)
  - test_htmx_post_rejects_missing_csrf (403)
  - test_htmx_post_invalid_universe_renders_error_flash (200 + alert-danger)
  - test_json_api_remains_json_after_ui_remediation (content-type application/json)
RESULT: 6/6 PASS (real HTTP layer via ASGITransport — actual UI/HTMX rendering test)

## FINDING 02 (P3) — F&O POINT-IN-TIME BUG
STATUS: FIXED
VERIFIED: _resolve_fno() queried DerivativeContract.underlying_stock_id.distinct()
with no availability filter. DerivativeContract.available_at (Date, NOT NULL, indexed)
confirmed in app/models/derivative_contract.py.
FIX (app/services/what_changed_screening_service.py::_resolve_fno):
  when as_of is not None → add .where(DerivativeContract.available_at <= as_of);
  when as_of is None → live (unfiltered) behavior preserved.
TESTS: tests/test_what_changed_screening_g11d4.py::TestScreeningFnoPointInTime
  (real Postgres test DB via db_session fixture):
  - test_contract_available_before_as_of_included
  - test_contract_available_on_as_of_included (<= boundary)
  - test_contract_available_after_as_of_excluded
  - test_no_as_of_preserves_live_universe
  - test_multiple_contracts_same_underlying_one_symbol
  - test_mixed_availability_pit_membership
  - test_empty_historical_fno_universe
RESULT: 7/7 PASS

## FINDING 03 (P3) — CONVERGENCE ORDERING / max_results
STATUS: FIXED
VERIFIED: screen() applied matched[:max_results] directly on gather order (universe
order), preventing higher-convergence matches from appearing.
FIX (app/services/what_changed_screening_service.py):
  pipeline is now resolve universe → evaluate → FILTER → DETERMINISTIC SORT →
  max_results truncation → response.
  New staticmethod _rank_key(): convergence_ratio DESC (negation), symbol ASC
  tie-break, None convergence deterministically LAST (flag (0|1, -ratio, symbol)).
  Only certified D-3 fields. No predictive score. No AI. Filtering happens before
  ranking; ranking before truncation. Summary counts still computed from the full run.
TESTS: tests/test_what_changed_screening_g11d4.py::TestScreeningConvergenceRanking
  - test_higher_convergence_wins_before_truncation (A=0.90,B=0.50,C=0.20,
    max_results=2 → A,B even when universe order is C,B,A)
  - test_equal_convergence_tie_break_symbol_asc
  - test_none_convergence_sorts_last_deterministically
  - test_none_convergence_tie_break_symbol_asc
  - test_no_matches_returns_empty_results
  - test_max_results_at_or_above_result_count_returns_all_ranked
  - test_summary_counts_reflect_full_run_not_truncation
RESULT: 7/7 PASS

## TEST RESULTS
TARGETED: uv run pytest tests/test_what_changed_screening_g11d4.py → 51 passed
G.11 REGRESSION: uv run pytest tests/test_what_changed_g11d1.py
  tests/test_what_changed_synthesis_g11d2.py
  tests/test_what_changed_interpretation_g11d3.py
  tests/test_what_changed_screening_g11d4.py → ALL PASSED (exit 0)
RUFF: uv run ruff check app tests → clean (exit 0)
GIT DIFF CHECK: git diff --check → clean (exit 0)
ALEMBIC: uv run alembic current → f1b9c8d7e6a5 (head)
APP BOOT: create_app() OK; route registration verified via openapi():
  /api/v1/stocks/screening/what-changed [GET, POST] (JSON, unchanged)
  /stocks/screening/what-changed [GET, POST] (HTML UI, new)
UI REGRESSION SANITY: tests/test_pages_ui.py, tests/test_productization_stage1.py,
  tests/test_ui_shell.py → all passed (run sequentially; concurrent pytest runs
  against the shared test DB cause truncation collisions — unrelated to this work)

## PROTECTED FILE STATUS: UNCHANGED
Compared via `git diff HEAD -- <file>` against 1cab27cb8ea8bf055b7b63dd069c14baced18933:
D-1: app/services/what_changed_service.py — UNCHANGED
D-1: app/schemas/what_changed.py — UNCHANGED
D-1: tests/test_what_changed_g11d1.py — UNCHANGED
D-2: app/services/what_changed_synthesis_service.py — UNCHANGED
D-2: app/services/what_changed_synthesis_config.py — UNCHANGED
D-2: app/schemas/what_changed_synthesis.py — UNCHANGED
D-2: tests/test_what_changed_synthesis_g11d2.py — UNCHANGED
D-3: app/services/what_changed_interpretation_service.py — UNCHANGED
D-3: app/services/what_changed_interpretation_config.py — UNCHANGED
D-3: app/schemas/what_changed_interpretation.py — UNCHANGED
D-3: app/ai/interpretation_narrative_service.py — UNCHANGED
D-3: tests/test_what_changed_interpretation_g11d3.py — UNCHANGED
ZERO modifications.

## AI SAFETY
include_ai_narrative=False remains hard-enforced at
app/services/what_changed_screening_service.py line 300 (worker interpret call).
No AI call affects state, direction, convergence, lifecycle, filtering, or ordering.

## FILES MODIFIED BY REMEDIATION (all uncommitted)
  M  app/api/routes/stocks_pages.py
  M  app/services/what_changed_screening_service.py        (untracked before)
  M  app/templates/components/what_changed_screener.html   (untracked before)
  A  app/templates/components/what_changed_screening_results.html (new)
  A  app/templates/what_changed_screening.html             (new)
  M  tests/test_what_changed_screening_g11d4.py            (untracked before)
Pre-existing D-4 state (unchanged by remediation): M app/api/routes/stocks.py,
untracked app/schemas/what_changed_screening.py,
app/services/what_changed_screening_config.py.

## REMAINING FINDINGS
None — all three findings fixed and verified.

## NEXT STEP
Independent re-QA / final certification. After certification is explicitly
requested, commit and push. Nothing is committed yet (per remediation rules).

## RESUMPTION QA EXTENSION (deepened pre-certification sweep)
State re-verified from the actual repository on resumption; nothing changed:
  - HEAD still 1cab27c on checkpoint/sprint19-20-20260822; zero commits/pushes
  - All 12 protected D-1/D-2/D-3 files: ZERO modifications (git diff HEAD -- <file>)
  - Targeted D-4 suite re-run: 51 passed (exit 0)
  - ruff app tests: All checks passed (exit 0)
  - git diff --check: exit 0
  - alembic current: f1b9c8d7e6a5 (head)
  - HTMX/CSRF convention regression (tests/test_product_browser_regressions.py):
    17 passed (exit 0) — confirms the global HTMX+CSRF conventions the new
    HTML POST route follows are intact
  - Consolidated G.11 regression re-run (D-1+D-2+D-3+D-4 in one pytest process):
    217 passed (exit 0)
All remediation findings remain fixed; nothing modified since the remediation.

## FINAL INDEPENDENT RE-QA + BROWSER CERTIFICATION (2026-09-19)
Playwright Chromium Live Browser Verification:
  - 14/14 Matrix Checks PASSED (Items A through N)
  - 0 console errors, 0 unhandled exceptions, 0 HTTP 500s
  - HTMX POST to /stocks/screening/what-changed confirmed returning HTML fragments (HTTP 200)
  - CSRF header auto-injection by shell verified
  - Validation error handling via danger flash confirmed
  - Responsive layouts (desktop, tablet, mobile) verified
  - Screenshots recorded under g11d4_screenshots/
  - Structured output logged to g11d4_browser_results.json

Automated Test Suites:
  - D-4 focused suite (tests/test_what_changed_screening_g11d4.py): 51/51 PASSED
  - Consolidated G.11 regression suite (D-1 + D-2 + D-3 + D-4): 217/217 PASSED
  - Ruff linter: All checks passed (clean)
  - git diff --check: clean
  - Alembic migration head: f1b9c8d7e6a5 (head)
  - Protected files diff against 1cab27c: 0 modifications across all 12 files
  - Application boot: create_app() clean (27 routes registered)

FINAL STATUS: CERTIFIED