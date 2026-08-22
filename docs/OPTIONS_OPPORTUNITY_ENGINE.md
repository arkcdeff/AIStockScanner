# OPTIONS OPPORTUNITY & EXACT STRIKE SELECTION ENGINE ARCHITECTURE

**Sprint:** 19.1G-A3-R1  
**Architecture Layer:** Quantitative Derivative Research Decision Layer  
**ORM Models Added:** `0` (Strictly derived, 56 total)  
**Alembic Head:** `g7h8i9j0k1l2 (head)`  

---

## 1. Executive Summary & Critical Decision Pipeline Hierarchy

Sprint 19.1G-A3-R1 refines and audits the options research-decision engine (`OptionOpportunityService`) for AIStockScanner.

### Mandatory Decision Pipeline Hierarchy
The engine evaluates options research candidates strictly top-down:
$$\text{Underlying Quality \& Context} \longrightarrow \text{Directional Assessment} \longrightarrow \text{CE / PE / NO\_TRADE}$$
$$\longrightarrow \text{Expiry Suitability} \longrightarrow \text{Moneyness \& Delta Fit} \longrightarrow \text{Exact Strike Ranking} \longrightarrow \text{Risk \& Vetoes}$$

```
                [UNDERLYING CONTEXT & DIRECTIONAL STRUCTURE]
                                     ↓
                    [DIRECTION: BULLISH / BEARISH / NEUTRAL]
                                     ↓
                          [SIDE: CE / PE / NO_TRADE]
                                     ↓
                      [EXPIRY SUITABILITY (Actual Expiries)]
                                     ↓
                      [MONEYNESS & DELTA FIT (ATM/ITM vs OTM)]
                                     ↓
                    [EXACT STRIKE RANKING & QUALITY SCORING]
                                     ↓
                      [RISK / LIQUIDITY / EVENT VETOES]
                                     ↓
                  [FINAL RESEARCH ASSESSMENT (OptionOpportunity)]
```

> [!IMPORTANT]
> **Direction Score Semantics: Bullishness Scale (0 to 100)**
> `direction_score` is a **Bullishness Scale**, NOT an absolute confidence strength percentage.
> - $\ge 65.0$: Bullish setup (`CE` side candidate)
> - $\le 35.0$: Bearish setup (`PE` side candidate)
> - $35.1 - 64.9$: Neutral or Conflicted setup (`NO_TRADE`)

> [!IMPORTANT]
> **No Single-Factor Direction Prohibition**
> Single factors (e.g., Call OI rising, PCR > 1.25, Spot above Gamma Flip, Max Pain, or highest OI strike) are evidence context ONLY. No single factor is permitted to dictate directional choice.

> [!IMPORTANT]
> **Side-Switching Protection**
> Recalculates direction from the complete evidence stack. If a bullish setup fails minimum score thresholds, the engine outputs `NO_TRADE` with explicit reasons—it NEVER automatically switches to `PE` (PUT).

---

## 2. Directional Structure Assessment (`OptionDirectionalAssessment`)

Direction is evaluated across 4 evidence families:
1. **Underlying Trend & Momentum (40%)**: Technical trend score and momentum score.
2. **Futures Buildup Structure (30%)**: Long Buildup ($+$), Short Buildup ($-$), Short Covering ($+$), Long Unwinding ($-$).
3. **Options PCR Context (15%)**: PCR OI $\ge 1.25$ (Bullish context), PCR OI $\le 0.70$ (Bearish context). Single factor cannot force direction.
4. **Options GEX & Scenario Context (15%)**: Scenario Net GEX positive (Dealer Long Gamma / Stabilizing), Scenario Net GEX negative (Dealer Short Gamma / Volatile).

### GEX Non-Directionality & Capped Heuristic Limitations
- **Gross Unsigned GEX**: Purely non-directional (0 directional points).
- **Scenario Net GEX**: Assumption-dependent heuristic. Capped at max 15.0 points contribution.
- **GEX Alone Protection**: If underlying trend, momentum, and futures buildup are neutral, GEX alone CANNOT force `BULLISH` or `BEARISH`. The engine outputs `NO_TRADE`.

---

## 3. Expiry Suitability & Event Risk (`OptionExpiryAssessment`)

- Evaluates actual imported contract expiries (`NEAREST`, `NEXT`, `MONTHLY`). Never synthesizes expiries.
- Scores expiries based on DTE and setup horizon:
  - **BTST (1-3 days)**: Prefers front expiry if $\text{DTE} \ge 1$. Overnight earnings or policy event triggers `EVENT_RISK_VETO` (`NO_TRADE`).
  - **SWING (1-3 weeks)**: Prefers $7 - 30$ DTE to avoid rapid theta decay. Front expiry with $\text{DTE} < 5$ receives a low DTE penalty, allowing the next expiry to be selected.
  - **BREAKOUT (1-4 weeks)**: Prefers $5 - 25$ DTE.

---

## 4. Moneyness & Delta Assessment (`OptionMoneynessAssessment`)

- Classifications: `DEEP_ITM`, `ITM`, `ATM`, `OTM`, `DEEP_OTM`.
- Uses `abs(delta)` for Delta magnitude suitability (CE $\text{Delta} = +0.50$ and PE $\text{Delta} = -0.50$ produce identical Delta fit scores):
  - $\text{abs(delta)} \in [0.40, 0.65]$ $\to$ `EXCELLENT`
  - $\text{abs(delta)} \in [0.30, 0.40)$ or $(0.65, 0.80]$ $\to$ `GOOD`
  - $\text{abs(delta)} < 0.20$ $\to$ Penalized with `LOW_DELTA_FAR_OTM`
  - $\text{abs(delta)} > 0.80$ $\to$ Penalized with `DEEP_ITM_HIGH_CAPITAL`

---

## 5. Exact Strike Ranking & Quality Scoring (`OptionStrikeAssessment`)

Contracts on the preferred side and expiry are scored out of 100 based on 4 weighted factor families:
1. **Delta Fit (35%)**: Scored on $\text{abs}(\text{abs(delta)} - 0.50)$.
2. **Moneyness Fit (25%)**: ATM/ITM $= 90$, OTM $= 60$, Deep OTM/ITM $= 20$.
3. **Liquidity (20%)**: High liquidity $= 90$, Medium $= 70$, Illiquid $= 30$.
4. **Theta Decay Burden (20%)**: Scored on $\text{theta\_burden\_pct} = \frac{|\text{daily\_theta}|}{\text{premium}} \times 100$. Safe handling for missing/zero premium (`None`).

### Hard Veto Conditions (`strike_score = 0.0`)
- `ILLIQUID_VETO`: Open Interest $< 100$ or illiquid classification.
- `LOW_DELTA_FAR_OTM`: $\text{abs(delta)} < 0.20$ or far OTM.
- `BREAKEVEN_OUTSIDE_EXPECTED_MOVE`: Breakeven price outside calculated expected move boundary.
- `MISSING_LOT_SIZE`: Historical lot size unavailable.
- `EVENT_RISK_VETO`: Pending earnings/policy risk for BTST profile.

---

## 6. Deterministic Tie-Breaking & Performance

- **Deterministic Tie-Break Hierarchy**: Score (desc) $\to$ High Liquidity (desc) $\to$ Contract ID (asc).
- **Historical Lot Premium**: $\text{estimated\_lot\_premium\_cost} = \text{price} \times \text{lot\_size}$ when verified PIT lot size exists; otherwise `None`.
- **Historical Spread**: Missing bid/ask is classified as `UNKNOWN`/`UNAVAILABLE` (not zero spread).
- **Performance**: Option chain, IV, Greeks, and exposure bulk-loaded once per request. Zero DB queries in contract evaluation loops ($O(N)$ memory execution).
