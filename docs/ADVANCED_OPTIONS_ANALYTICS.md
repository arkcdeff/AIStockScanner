# ADVANCED OPTIONS FOUNDATION (IV + GREEKS + VOLATILITY STRUCTURE + EXPECTED MOVE + LIQUIDITY)

**Sprint:** 19.1G-A1-R1  
**Architecture Layer:** Quantitative Derivative Analytics  
**ORM Models Added:** `0` (Strictly derived, 56 total)  
**Alembic Head:** `g7h8i9j0k1l2 (head)`  

---

## 1. Overview & Verification Pass

Sprint 19.1G-A1-R1 performs a rigorous numerical verification pass over the quantitative options engine:
- Analytical Black-Scholes formulas for Call/Put pricing and standard Option Greeks ($\Delta, \Gamma, \Theta, \mathcal{V}, \rho$).
- Textbook reference validation against independently derived benchmark values ($S=100, K=100, T=1, r=0.05, q=0, \sigma=0.20$).
- Exact Put-Call Parity cross-check ($C - P = S e^{-q T} - K e^{-r T}$).
- Square-Root-of-Time scaling invariant verification for Expected Move ($S \times \text{IV} \times \sqrt{\text{DTE}/365}$).
- Rule-of-thumb heuristic labeling for ATM straddle expected move (`ATM_STRADDLE_85PCT_HEURISTIC`).
- Transparent risk-free rate metadata hierarchy (`CONFIGURED_FALLBACK` / `APPROXIMATION`).
- Dividend yield assumption metadata (`ZERO_DIVIDEND_ASSUMPTION`).
- Arbitrage bounds enforcement (`ABOVE_THEORETICAL_BOUND` and `INTRINSIC_VIOLATION`).

---

## 2. Hardcoded Textbook Reference Benchmarks

For standard reference inputs: $S = 100.0, K = 100.0, T = 1.0, r = 0.05, q = 0.0, \sigma = 0.20$:

| Analytical Quantity | Exact Theoretical Value | Units / Convention |
| :--- | :--- | :--- |
| **Call Price ($C$)** | $10.45058$ | Currency units |
| **Put Price ($P$)** | $5.57352$ | Currency units |
| **Call Delta ($\Delta_{CE}$)** | $+0.63683$ | Price change per $1.0$ underlying move |
| **Put Delta ($\Delta_{PE}$)** | $-0.36317$ | Price change per $1.0$ underlying move |
| **Gamma ($\Gamma$)** | $+0.01876$ | Delta change per $1.0$ underlying move (identical CE/PE) |
| **Vega per 1% IV ($\mathcal{V}_{1\%}$)** | $+0.37524$ | Price change per $+1.0\%$ change in IV |
| **Daily Call Theta ($\Theta_{CE, \text{day}}$)** | $-0.01756$ | Price decay per calendar day ($\text{Annual } \Theta / 365$) |
| **Daily Put Theta ($\Theta_{PE, \text{day}}$)** | $-0.01071$ | Price decay per calendar day ($\text{Annual } \Theta / 365$) |
| **Call Rho per 1% Rate ($\rho_{CE, 1\%}$)** | $+0.53232$ | Price change per $+1.0\%$ change in interest rate |
| **Put Rho per 1% Rate ($\rho_{PE, 1\%}$)** | $-0.41834$ | Price change per $+1.0\%$ change in interest rate |

### Put-Call Parity Invariant
$$C - P = 10.45058 - 5.57352 = 4.87706 = 100 - 100 e^{-0.05 \times 1.0} = 4.87706$$

---

## 3. Expected Move Engine & Square-Root-of-Time Scaling

1. **Primary Method (`PRIMARY_IV_SQRT_TIME`)**:
   $$\text{Expected Move} = S \times \text{IV}_{\text{ATM}} \times \sqrt{\frac{\text{DTE}}{365}}$$
   - **Square-Root-of-Time Invariant**: For identical spot and IV, quadrupling DTE ($4 \times \text{DTE}$) yields exactly double ($2 \times$) the expected move points, reflecting diffusion kinetics ($\sigma \sqrt{T}$).
2. **Straddle Method (`ATM_STRADDLE_85PCT_HEURISTIC`)**:
   $$\text{Straddle Move} = 0.85 \times (\text{Call}_{\text{ATM}} + \text{Put}_{\text{ATM}})$$
   - Documented explicitly as a rule-of-thumb market heuristic, distinct from Black-Scholes theoretical identities.

---

## 4. Risk-Free Rate & Dividend Metadata Hierarchy

- **Risk-Free Rate Metadata (`RiskFreeRateInfo`)**:
  - `rate`: Configured rate ($0.065 = 6.5\%$ p.a. default).
  - `rate_source`: Marked `"CONFIGURED_FALLBACK"` unless explicitly supplied by caller or historical series (`"EXPLICIT_PARAMETER"`, `"HISTORICAL_PIT_SERIES"`).
  - `rate_quality`: Marked `"APPROXIMATION"` for fallback configurations to prevent false claims of official RBI/T-Bill provenance.
- **Dividend Yield Metadata**:
  - Exposes `dividend_yield` ($0.0$ default) and `dividend_assumption` (`"ZERO_DIVIDEND_ASSUMPTION"`).

---

## 5. Arbitrage Bounds & Implied Volatility Solver

- **Intrinsic Violation Guard**: If $\text{Market Price} < \max(0, S e^{-q T} - K e^{-r T}) - 10^{-4}$, returns `solver_status="INTRINSIC_VIOLATION"` and `input_quality="BELOW_INTRINSIC"`.
- **Theoretical Upper Bound Guard**: If Call Price $> S e^{-q T}$ or Put Price $> K e^{-r T}$, returns `solver_status="ABOVE_THEORETICAL_BOUND"` and `input_quality="ABOVE_SPOT_MAXIMUM"`.
- **Zero/Expired DTE Guard**: Returns `solver_status="ZERO_DTE"` without mathematical division-by-zero or `NaN`/`Infinity` errors.
