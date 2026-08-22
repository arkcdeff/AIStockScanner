# ADVANCED OPTIONS EXPOSURE ANALYTICS ARCHITECTURE

**Sprint:** 19.1G-A2 / 19.1G-A2-R1 Audit Pass  
**Architecture Layer:** Quantitative Derivative Exposure Engine  
**ORM Models Added:** `0` (Strictly derived, 56 total)  
**Alembic Head:** `g7h8i9j0k1l2 (head)`  

---

## 1. Executive Summary & Semantic Rules

Sprint 19.1G-A2 / 19.1G-A2-R1 extends the verified Black-Scholes foundation to deliver point-in-time-safe options exposure metrics:
- Gross Gamma Exposure (Gross GEX)
- Scenario-Signed Estimated GEX
- Gross Delta Exposure (Gross DEX)
- Gross Vanna & Charm Exposure
- Spot Scenario Gamma Profile ($S \pm 10\%$, $0.5\%$ grid steps, **41 inclusive grid points**, `STATIC_IV`)
- Scenario Gamma Flip (Zero-Gamma level) detection
- Gamma Concentration metrics (Top 1, 3, 5 strikes share)
- Stock 360° `AdvancedExposureContext` integration

### Critical Semantic Rule: Aggregate OI Positioning
Aggregate Open Interest (OI) represents total outstanding contracts in the market. Every contract has both a long and a short side. **Aggregate OI does NOT reveal actual dealer or market-maker position direction.**
1. **Factual Magnitude Layer (`GROSS_UNSIGNED`)**: Unsigned gross exposure metrics represent non-directional market sensitivity.
2. **Scenario Layer (`DEALER_SHORT_CALL_LONG_PUT_HEURISTIC`)**: Signed exposure metrics apply an explicit positioning assumption. Every signed output exposes `assumption_profile` and user warnings.
3. **Gamma Flip Requires Signed Scenario**: Gamma Flip zero-crossing detection is mathematically inapplicable on nonnegative unsigned gross GEX. Calling `get_gamma_flip` with `assumption_profile = "GROSS_UNSIGNED"` returns `status = "SIGNED_ASSUMPTION_REQUIRED"` and `gamma_flip_level = None`.
4. **Mandatory Disclaimer**: UI renders a prominent warning stating that signed exposure is a scenario-based estimate, NOT observed dealer positioning.

---

## 2. Mathematical Formulas & Conventions

### A. Gross Gamma Exposure (Gross GEX)
$$\text{Gross GEX} = \Gamma \times \text{Open Interest} \times \text{Historical Lot Size} \times S^2 \times 0.01$$
- $\Gamma$: Black-Scholes Gamma ($\Delta$ change per $1.0$ spot move)
- Units: `INR_NOTIONAL_1PCT_SPOT_MOVE`

### B. Scenario-Signed GEX
Under `DEALER_SHORT_CALL_LONG_PUT_HEURISTIC`:
- $\text{Scenario Call GEX} = -\text{Gross Call GEX}$
- $\text{Scenario Put GEX} = +\text{Gross Put GEX}$
- $\text{Scenario Net GEX} = \text{Scenario Call GEX} + \text{Scenario Put GEX}$

### C. Gross Delta Exposure (Gross DEX)
$$\text{Gross DEX} = |\Delta| \times \text{Open Interest} \times \text{Historical Lot Size} \times S$$
- Units: `INR_NOTIONAL`

### D. Analytical Vanna & Charm
- **Vanna ($\mathcal{V}_a = \frac{\partial \Delta}{\partial \sigma}$)**:
  $$\text{Vanna}_{\text{raw}} = -e^{-q T} n(d_1) \frac{d_2}{\sigma}, \quad \text{Vanna}_{1\%} = \frac{\text{Vanna}_{\text{raw}}}{100.0}$$
  - Unit: Rate of Delta change per $+1.0\%$ change in volatility ($\sigma$).
- **Charm ($\mathcal{C}_h = \frac{\partial \Delta}{\partial t} = -\frac{\partial \Delta}{\partial T}$)**:
  $$\text{Charm}_{\text{annual}} = \pm q e^{-q T} N(\pm d_1) - e^{-q T} n(d_1) \left[ \frac{2(r - q) T - d_2 \sigma \sqrt{T}}{2 T \sigma \sqrt{T}} \right], \quad \text{Charm}_{\text{day}} = \frac{\text{Charm}_{\text{annual}}}{365.0}$$
  - Time/Sign Convention: Annual rate of Delta change per passage of calendar time ($t$), presented per calendar day ($\frac{\text{Annual Charm}}{365.0}$).
- **Vanna & Charm Exposures**:
  $$\text{Gross Vanna Exposure} = |\text{Vanna}_{1\%}| \times \text{OI} \times \text{Lot} \times S$$
  $$\text{Gross Charm Exposure} = |\text{Charm}_{\text{day}}| \times \text{OI} \times \text{Lot} \times S$$

---

## 3. Spot Scenario Grid & Gamma Flip Interpolation

1. **Spot Grid Parameters**:
   - `spot_range_pct`: $10.0\%$ (bounded $[1.0, 30.0]$)
   - `spot_step_pct`: $0.5\%$ (bounded $[0.1, 5.0]$)
   - **Reconciled Point Count**: Exactly **41 inclusive grid points** (from $-10.0\%$ to $+10.0\%$ in steps of $0.5\%$).
   - Current spot ($0.0\%$ change) exists exactly once at step index 20.
2. **Static-IV Assumption (`STATIC_IV`)**: Holds each contract's Black-Scholes IV constant while repricing $d_1, d_2, \Gamma$ across hypothetical spot levels.
3. **Zero-Crossing Linear Interpolation**:
   $$S^* = S_1 + (0.0 - \text{GEX}_1) \times \frac{S_2 - S_1}{\text{GEX}_2 - \text{GEX}_1}$$
4. **No Crossing Status**: Returns `status="NO_FLIP_IN_RANGE"` if no zero-crossing occurs in $[-10\%, +10\%]$.
5. **Multiple Crossings**: Identifies all zero-crossings and designates the crossing nearest to current spot as primary (`status="MULTIPLE_FLIPS_FOUND"`, deterministic tie-break).

---

## 4. Point-In-Time (PIT) Safety & Historical Lot-Size Hierarchy

- All raw option facts, spot prices, contracts, and lot sizes enforce point-in-time constraints (`available_at <= as_of_date` and `effective_from <= trade_date`).
- **Lot-Size Hierarchy for Trade Date $T$**:
  1. Historical record valid for $T$ in `DerivativeLotSizeHistory` (`effective_from <= T`, `effective_to IS NULL OR effective_to >= T`, `available_at <= as_of`) $\to$ `lot_size_source = "HISTORICAL_PIT"`, `lot_size_quality = "VERIFIED_HISTORICAL"`.
  2. Contract default provably effective for $T$ (`available_at <= T`) $\to$ `lot_size_source = "SOURCE_VERIFIED"`, `lot_size_quality = "SOURCE_VERIFIED"`.
  3. Otherwise lot size cannot be historically established $\to$ `lot_size_source = "UNAVAILABLE"`, `lot_size_quality = "UNAVAILABLE"`.
- **Missing Lot-Size Behavior**: If lot size is `UNAVAILABLE`, lot-dependent exposures (GEX, DEX, Vanna, Charm) are returned as `None` (`NOT_AVAILABLE`). Guessed or leaked current lot sizes are **NEVER** used (`MISSING != ZERO`).
