# DXY Pair Strategy — Trading Rules
### Justin Clatworthy | The Trading Academy

---

Clearly written rules for the DXY-based pair correlation strategy. This document serves as the
definitive checklist for live trading and the reference point for any future backtesting work.
It should be reviewed whenever parameters are re-optimised or trade types are added.

---

## Overview

| | |
|---|---|
| **Strategy Name** | DXY Pair Strategy (JC) |
| **Signal Instrument** | DXY (US Dollar Index) — 15-minute chart |
| **Execution Pairs** | EURUSD, GBPUSD, AUDUSD, NZDUSD, USDJPY, USDCAD, USDCHF, XAUUSD |
| **Backtest Period** | Aug 2023 – Apr 2026 (32.4 months) |
| **Risk Per Trade** | 0.25% of account per pair |
| **Trade Types** | GAP_REJ · REV · LON_ATTR · ATTR_CORE |

---

## Core Principle

Trades are **never taken directly on DXY**. DXY is the signal instrument only.
When DXY produces a valid entry signal, correlated pair positions are entered at the
close of the DXY signal bar.

**Pair correlation direction:**

| Pair | Type | DXY LONG signal | DXY SHORT signal |
|---|---|---|---|
| USDJPY, USDCAD, USDCHF | USD is base | Go **LONG** | Go **SHORT** |
| EURUSD, GBPUSD, AUDUSD, NZDUSD | USD is quote | Go **SHORT** | Go **LONG** |
| XAUUSD | Commodity | Go **SHORT** | Go **LONG** |

Direction and tick-factor conversion are **auto-detected from the chart symbol** in both
`DXYPairStrategy.pine` and `DXYPairLevels.pine` — no manual reconfiguration is needed
when switching between pairs.

**Label placement:** long pair trades show a label **below** the signal candle (▲).
Short pair trades show a label **above** the signal candle (▼).

**Exit rule (critical):** When DXY hits its own TP or SL level, **exit all pair positions
immediately** — regardless of where each pair stands at that moment. There are no
pair-level TP or SL targets. The DXY exit is the only exit trigger.

---

## Preparation (Anticipate)

Follow the ACE methodology: **Anticipate → Confirm → Execute.**
The anticipation phase prevents reactive trading and builds consistency.

1. **Weekly preparation (Sunday evening):** Review the economic calendar on
   Forex Factory for the coming week. Identify any high-impact USD news days
   (NFP, FOMC, CPI, etc.) that may disrupt normal DXY behaviour or create
   spike risk around the London open window.

2. **Daily preparation (before 07:00 UTC):** On the DXY 15-minute chart, mark
   today's London session opening level (07:00 UTC, or 06:30 UTC on Monday).
   This is the primary reference level for both REV and LON_ATTR trades.
   The yellow zone box on the chart (zone_bot to zone_top of the London open
   candle body) is the LON_ATTR pristine zone — note whether it is still intact.

3. **Check the Tokyo gap:** Note whether there was a gap at the 23:45 UTC Tokyo
   open vs. the 23:15 UTC reference close.
   - If the gap has **already been touched** → GAP_REJ setup is active from 06:00 UTC.
   - If the gap has **not yet been touched** → ATTR_CORE setup becomes active once
     London open is seen and the CORE filters are met.

4. **Assess HTF regime:**
   - 4H Bollinger Bands: flat/contracting = GAP_REJ and ATTR_CORE allowed; expanding = skip both.
   - 1H Bollinger Bands: expanding with MA sloping up = REV LONG valid;
     expanding with MA sloping down = REV SHORT valid; flat = skip REV.

5. **News check:** If there is a high-impact USD event during the London session
   window, decide in advance: skip the session entirely, or only take trades that
   complete before the release. If already in a trade when a release approaches,
   consider exiting before the event. **The GAP_REJ label on the chart will
   always show "CHECK NEWS!" as a reminder.**

---

## Trade Type 1: GAP_REJ — Gap Rejection

### What it is
Tokyo session opens with a gap relative to the 23:15 UTC reference close.
Price fills the gap (touches the reference close), then pulls back. The pullback
confirmation candle is the entry. We are fading the gap fill — trading the
rejection of the gap target, not the gap fill itself.

### Anticipate
- Tokyo opened with a gap ≥ **75 pts** (absolute value of 23:45 open minus 23:15 close).
- The gap has already been **filled** this session — price has touched or crossed
  the gap target (attr_gap_touched = true). **This is what separates GAP_REJ from
  ATTR_CORE — we wait for the fill first.**
- Price has pulled back away from the gap target (below for gap-down, above for gap-up).
- Prior session DXY range was ≤ 8,000 pts (not an unusually large range day).
- 4H Bollinger Bands are **flat** (non-expanding) — no trending market.

### Confirm
- **No signal = no trade.** Wait for a confirmation candle in the pullback direction:
  - Bullish engulfing, bull pin bar, or 3-bar reversal (for LONG).
  - Bearish engulfing, bear pin bar, or 3-bar reversal (for SHORT).
- Minimum reward from entry to TP ≥ **100 pts** at the time of the signal.

### Execute
- **Entry:** Close of the confirmation candle.
- **TP (LONG):** Gap target (23:15 reference close) minus 50 pt near-edge buffer.
- **TP (SHORT):** Gap target plus 50 pt near-edge buffer.
- **SL:** 1:1 mirror of the TP distance on the opposite side of entry.
- **R:R:** 1:1.
- One signal per session (resets at the 23:45 Tokyo open). Shared with ATTR_CORE.

### Session Window
06:00–19:30 UTC (Tue–Fri) · 06:30–19:30 UTC (Monday)
Japan session (23:45–06:00 UTC) excluded.

---

## Trade Type 2: REV — London Open Reversal

### What it is
Price moves away from the London session opening price by a meaningful distance,
then reverses and returns to it. The 1H Bollinger Bands must be expanding in the
direction of the return move, confirming genuine momentum rather than noise.
The stop loss is structural — placed beyond the prior calendar day's high or low.

### Anticipate
- London open price has been set (07:00 UTC bar open, or 06:30 UTC Monday).
- Price has moved **at least 400 pts** away from the opening level this session
  (max_move_down ≥ 400 pts for LONG; max_move_up ≥ 400 pts for SHORT).
- Price has returned to within **250 pts** of the opening level.
- Prior calendar day's low (LONG) or high (SHORT) is available for structural SL placement.

### Confirm
- **No signal = no trade.** Wait for a confirmation candle at the opening level:
  - Bullish engulfing, bull pin bar, or 3-bar reversal (LONG).
  - Bearish engulfing, bear pin bar, or 3-bar reversal (SHORT).
- 1H BB must be **expanding** with MA sloping in the trade direction:
  - LONG: 1H BB expanding, MA slope up.
  - SHORT: 1H BB expanding, MA slope down.
- If the opening has been bridged repeatedly and price is oscillating around it
  with no clear direction, consider standing aside for the session.

### Execute
- **Entry:** Close of the confirmation candle.
- **SL (LONG):** Prior calendar day low minus 50 pt buffer. Capped at 3,000 pts from entry.
- **SL (SHORT):** Prior calendar day high plus 50 pt buffer. Capped at 3,000 pts from entry.
- **TP:** 1:1 mirror of the SL distance on the opposite side of entry.
- **R:R:** 1:1.

### Session Window
07:00–12:00 UTC (Tue–Fri) · 06:30–12:00 UTC (Monday)
Japan session excluded. Window closes at 12:00 UTC.

---

## Trade Type 3: LON_ATTR — London Open Attraction

### What it is
DXY has moved far from the London opening candle body zone and prints a pin bar
pointing back toward it. The zone must be **pristine** — no candle (outside Japan
session) has opened or closed through the zone edge since London open. The trade
attracts back toward the near edge of the candle body.

### Anticipate
- London open candle body zone is defined each session:
  - **zone_top** = math.max(open, close) of the London open candle.
  - **zone_bot** = math.min(open, close) of the London open candle.
  - The **yellow box** on the chart shows this zone. Yellow = pristine. Grey = violated.
- DXY close is **≥ 1,000 pts below** the London open price (LONG setup), OR
  **≥ 1,000 pts above** the London open price (SHORT setup).
- Zone is **pristine**:
  - LONG: no candle open or close has touched or exceeded zone_top since London open.
  - SHORT: no candle open or close has touched or gone below zone_bot since London open.
  - Once the zone turns grey on the chart, LON_ATTR is no longer valid for that direction.

### Confirm
- **Pin bar only** — engulfing and 3-bar reversals are not accepted for LON_ATTR:
  - **Bull pin bar (LONG):** lower wick ≥ 2× body size AND lower wick ≥ 1.5× upper wick.
  - **Bear pin bar (SHORT):** upper wick ≥ 2× body size AND upper wick ≥ 1.5× lower wick.
- TP validity check: zone_bot must be above entry for LONG / below entry for SHORT.
  If this condition fails the signal is invalid.

### Execute
- **Entry:** Close of the pin bar candle.
- **TP (LONG):** zone_bot — the near (lower) edge of the London open candle body.
- **TP (SHORT):** zone_bot — the far (lower) edge of the London open candle body.
  Note: for SHORT the full body width must be traversed to reach TP.
- **SL:** 1:1 mirror of the TP distance below entry (LONG) or above entry (SHORT).
- **R:R:** 1:1.
- One signal per session (resets at each London open).
- No BB regime filter required.

### Session Window
Strictly after London open until 18:00 UTC (Tue–Fri) · 06:30–18:00 UTC (Monday)
Japan session excluded.

---

## Trade Type 4: ATTR_CORE — Pristine Zone Approach

### What it is
The Tokyo gap zone has **not yet been touched** since the 23:45 open — the zone is
pristine. Unlike GAP_REJ (which waits for the fill), ATTR_CORE enters while price is
still approaching the zone from outside. Two CORE filters distinguish high-quality
setups from noise: DXY must have been close to the zone when London opened
(gap ≤ 1,500 pts), and must not have already extended significantly further away
since (wave extension ≤ 1,500 pts). A 3-bar impulsive move toward the zone confirms
active directional pressure.

**Direction is instrument-specific:** 32-month analysis shows the setup performs well
only in one direction per instrument:
- **XAUUSD:** DXY LONG signals only → pair trade is SHORT. (66.7% WR)
- **All other pairs:** DXY SHORT signals only → pair trade direction follows standard
  correlation (USD-quote pairs go LONG; USD-base pairs go SHORT). (68.6% WR)

### Anticipate
- Tokyo opened with a gap ≥ **75 pts** and the gap target has **not** been touched
  this session (zone is pristine — attr_gap_touched = false).
- The London open bar has been seen — the CORE measurement is now active.
- **CORE Filter 1 — Gap at London open ≤ 1,500 pts:** DXY was within 1,500 pts of
  the gap target at the moment London opened. Wide gap = price never got close = poor
  setup quality.
- **CORE Filter 2 — Wave extension ≤ 1,500 pts:** Since the London open, DXY has not
  moved more than 1,500 pts further away from the zone. Large extension = the move
  has overextended away before approaching = poor setup quality.
- Price is still on the correct side of the gap target (below for gap-down, above for gap-up).
- Prior session DXY range was ≤ 8,000 pts.
- 4H Bollinger Bands are **flat** (non-expanding).

### Confirm
- **3-bar approach ≥ 150 pts:** DXY must have moved at least 150 pts toward the zone
  over the last 3 bars, confirming impulsive directional pressure (not just drifting).
- **Confirmation candle** in the approach direction:
  - Bullish engulfing, bull pin bar, or 3-bar reversal (DXY LONG / XAUUSD).
  - Bearish engulfing, bear pin bar, or 3-bar reversal (DXY SHORT / all others).
- Minimum reward from entry to TP ≥ **100 pts** at the time of the signal.

### Execute
- **Entry:** Close of the confirmation candle.
- **TP (DXY LONG / ATTR_CORE LONG):** Gap target minus 50 pt near-edge buffer.
- **TP (DXY SHORT / ATTR_CORE SHORT):** Gap target plus 50 pt near-edge buffer.
- **SL:** 1:1 mirror of the TP distance on the opposite side of entry.
- **R:R:** 1:1.
- One signal per session (resets at 23:45 Tokyo open). Shared counter with GAP_REJ —
  only one of GAP_REJ or ATTR_CORE can fire per session (GAP_REJ requires the zone
  to have been touched; ATTR_CORE requires it not to have been touched).

### Session Window
06:00–19:30 UTC (Tue–Fri) · 06:30–19:30 UTC (Monday)
Japan session (23:45–06:00 UTC) excluded.

---

## News & Fundamentals Rules

| Situation | Action |
|---|---|
| High-impact USD news within 30 min of signal | Skip the trade |
| Already in a trade, news approaching | Consider exiting before release |
| NFP / FOMC day | Consider skipping entire session |
| GAP_REJ label always shows "CHECK NEWS!" | Manual check required before every GAP_REJ entry |

---

## TradingView Indicators & Scripts

| Script | Purpose |
|---|---|
| `DXYTradeAlert.pine` | Live trading — alerts, visual zones, TP/SL lines. Apply to DXY 15-minute chart. |
| `DXYPairLevels.pine` | Live trading — applies DXY signals to a pair chart. Draws estimated pair TP/SL lines and fires webhook JSON alerts. Run on any supported pair 15-minute chart. Auto-detects direction and tick factor from the chart symbol. |
| `DXYZoneStrategy.pine` | Backtest (DXY chart) — strategy tester version. Records trades, win rate, P&L. |
| `DXYPairStrategy.pine` | Backtest (pair chart) — applies DXY signals to each pair via request.security. Run on any supported pair 15-minute chart. Auto-detects direction and tick factor from the chart symbol. |

---

## Python Backtesting Scripts

| Script | Purpose |
|---|---|
| `test_parameter_sweep.py` | Full parameter sweep: GAP_REJ min_gap, REV proximity, REV min_move. Single scan, filters pool at each threshold. Primary optimisation script. |
| `test_gap_rej_sweep.py` | GAP_REJ only sweep (legacy — superseded by test_parameter_sweep.py). |

### Recommended Backtesting Schedule

| Frequency | Task |
|---|---|
| **Monthly** | Run `test_parameter_sweep.py`. Confirm the three key parameters (min_gap, proximity, min_move) are still sitting at or near their optimal thresholds. Flag if the optimum shifts by more than one threshold step. |
| **Quarterly** | Run the full period through `DXYPairStrategy.pine` on all supported pairs in TradingView Strategy Tester. Compare net R and win rate to baseline. Investigate if win rate drops >5 percentage points or net R per month falls >20% vs. backtest average. |
| **After a major regime change** | If DXY enters a prolonged one-directional trend or volatility compresses significantly for several weeks, re-run `test_parameter_sweep.py` and consider whether the REV min_move or LON_ATTR distance thresholds need adjusting. |
| **After adding a new trade type** | Run a full parameter sweep before going live. Document results in RULES.JSON under the new rule's `backtest_performance` block. |

**Note:** The proximity sweep was run with min_move fixed at 100 pts, and the min_move sweep was run with proximity fixed at 150 pts. The two parameters have not been co-optimised simultaneously. A full grid sweep across both REV parameters simultaneously is recommended as the next optimisation step.

---

## Parameter Defaults & Backtest Performance Summary

### Active Parameter Defaults

| Parameter | Trade Type | Default Value | Optimised? |
|---|---|---|---|
| Min gap at Tokyo open | GAP_REJ / ATTR_CORE | **75 pts** | Yes |
| Min reward to TP | GAP_REJ / ATTR_CORE | 100 pts | Fixed |
| Near-edge TP buffer | GAP_REJ / ATTR_CORE | 50 pts | Fixed |
| Max prior session range | GAP_REJ / ATTR_CORE | 8,000 pts | Fixed |
| **Max gap at London open** | **ATTR_CORE** | **1,500 pts** | CORE filter |
| **Max wave extension since London open** | **ATTR_CORE** | **1,500 pts** | CORE filter |
| **Min 3-bar approach** | **ATTR_CORE** | **150 pts** | CORE filter |
| Max distance from opening level | REV | **250 pts** | Yes — captures early approach entries |
| Min move away before return | REV | **400 pts** | Yes — filters genuine reversals from noise |
| Structural SL buffer | REV | 50 pts | Fixed |
| Max SL distance (hard cap) | REV | 3,000 pts | Fixed |
| Min distance from London open (LONG) | LON_ATTR | **1,000 pts** | Yes |
| Min distance from London open (SHORT) | LON_ATTR | **1,000 pts** | Yes |

### Backtest Performance by Trade Type
*32.4-month period: Aug 2023 – Apr 2026. Pair net R = fractional R across the tested pairs.*

| Trade Type | DXY Win Rate | Pair Net R | N (pair trades) | Notes |
|---|---|---|---|---|
| **GAP_REJ** | 43.5% | +51.1R | 78 | EU +10.2 · UJ +16.7 · UC +12.0 · XAU +12.2 |
| **REV** (prox=250, move=100) | 50.0% | +69.4R | 294 | Optimised proximity only |
| **REV** (prox=150, move=400) | — | +55.9R | 85 | EU +8.6 · UJ +5.1 · UC +20.8 · XAU +21.4 |
| **LON_ATTR** | 57.1% | +95.1R | 83 | EU +7.6 · UJ +37.8 · UC +13.3 · XAU +36.4 |
| **ATTR_CORE** | **70.2%** | **+65.46R** | — | XAUUSD DXY LONG only (66.7% WR) · all others DXY SHORT only (68.6% WR) |
| **Portfolio (original params)** | **56.4%** | **+165.7R** | — | Profit factor 1.68 · Ann. return ~15.3% |

*Original params baseline: gap=150 pts, prox=150 pts, move=100 pts, lon_attr=1,000 pts.*
*Individual trade-type figures use optimised params for that type; combined portfolio figure uses original params.*
*The two REV rows show single-parameter sweeps independently — a combined prox=250 + move=400 grid has not yet been validated. This is the recommended next step.*
*ATTR_CORE portfolio figure does not yet include the full 8-pair combination — instrument coverage may expand this figure.*

### Key Observations
- **ATTR_CORE** has the highest win rate of any signal type at 70.2%. The CORE filters (gap-at-London and wave-extension) are strong predictors — the unfiltered pristine gap fill backtested at −9.5R; the filtered version produces +65.46R.
- **LON_ATTR** is the highest-returning trade type per signal count and has the second-best DXY win rate (57.1%). USDJPY and XAUUSD contribute the most.
- **REV** generates the most signals and strongest absolute return when proximity is widened to 250 pts. Raising min_move to 400 pts improves win rate significantly (54.9% → 68.2%) at the cost of fewer trades.
- **GAP_REJ** has the lowest DXY win rate (43.5%) but is profitable at the pair level — the pair-exit approach captures asymmetric moves that DXY alone does not.
- **GAP_REJ and ATTR_CORE are mutually exclusive per session:** GAP_REJ requires the zone to have been touched; ATTR_CORE requires it not to have been touched. Only one can fire per Tokyo session.
- **No trade should be taken without a confirmation candle. No signal = no trade.**

---

*Document version: May 2026. Parameters reflect optimisation completed May 2026.*
*Source files: RULES.JSON · test_parameter_sweep.py · DXYTradeAlert.pine · DXYZoneStrategy.pine · DXYPairStrategy.pine · DXYPairLevels.pine*
