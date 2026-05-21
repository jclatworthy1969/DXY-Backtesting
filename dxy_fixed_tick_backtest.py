"""
DXY Fixed-Tick Backtest
=======================
Tests a fixed ±200-DXY-tick 1:1 R:R on all 8 correlated pairs,
triggered purely by DXY signal detection.

The DXY win/loss is completely ignored — the test asks only:
  "When the DXY fires a signal, do the 8 pairs move in the expected
   direction over a standardised ±200-tick distance?"

Fixed 200-tick conversion (DXY sl_d = 0.200):
    5dp FX pairs (EUR/GBP/AUD/NZD/CAD/CHF) : 0.200 × 0.01  = 0.00200 (20 pips)
    USDJPY                                  : 0.200 × 1.0   = 0.200   (20 JPY pips)
    XAUUSD                                  : 0.200 × 100.0 = 20.00   ($20/oz)

Position sizing:
    Account    : $100,000
    Risk/trade : 0.125% = $125 per pair per signal
    1:1 RR     → Win = +$125   Loss = -$125   Timeout = $0
"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from dxy_backtest import (
    CSV_PATH, ATTR_ENABLED, ATTR_MIN_PTS, ATTR_MAX_PTS, ZONE_MIN_GAP,
    REV_ENABLED, REV_MIN_SL, REV_MAX_DIST, REV_MIN_BODY, REV_MIN_RANGE,
    ENTRY_START_H, ENTRY_START_M, ENTRY_END_H, ENTRY_END_M,
    REV_END_H, REV_END_M, MONDAY_START_H, JAPAN_END_H,
    USE_ENGULF, USE_PIN, USE_3BAR, PIN_WICK_MULT,
    DIV_LOOKBACK, REV_MIN_DIV, USE_ADX_GATE, ADX_MIN,
    MAX_LOOKFORWARD, EXIT_MODE,
    compute_indicators, div_score_bull, div_score_bear,
    candle_patterns, session_flags, form_zone, resolve_trade,
)

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(CSV_PATH)

PAIR_FILES = {
    'EURUSD': os.path.join(BASE_DIR, 'FX_EURUSD, 15 (1).csv'),
    'GBPUSD': os.path.join(BASE_DIR, 'FX_GBPUSD, 15 (1).csv'),
    'AUDUSD': os.path.join(BASE_DIR, 'FX_AUDUSD, 15 (1).csv'),
    'NZDUSD': os.path.join(BASE_DIR, 'FX_NZDUSD, 15 (1).csv'),
    'USDCAD': os.path.join(BASE_DIR, 'FX_USDCAD, 15 (1).csv'),
    'USDCHF': os.path.join(BASE_DIR, 'FX_USDCHF, 15 (1).csv'),
    'USDJPY': os.path.join(BASE_DIR, 'FX_USDJPY, 15 (1).csv'),
    'XAUUSD': os.path.join(BASE_DIR, 'FX_XAUUSD, 15 (1).csv'),
}

PAIR_DIRECTION = {
    'EURUSD': -1, 'GBPUSD': -1, 'AUDUSD': -1, 'NZDUSD': -1,
    'USDCAD': +1, 'USDCHF': +1, 'USDJPY': +1,
    'XAUUSD': -1,
}

DXY_TICK  = 0.001
PAIR_TICK = {
    'EURUSD': 0.00001, 'GBPUSD': 0.00001, 'AUDUSD': 0.00001, 'NZDUSD': 0.00001,
    'USDCAD': 0.00001, 'USDCHF': 0.00001,
    'USDJPY': 0.001,
    'XAUUSD': 0.01,
}
XAUUSD_MULT = 10
PAIR_FACTOR = {
    p: (PAIR_TICK[p] / DXY_TICK) * (XAUUSD_MULT if p == 'XAUUSD' else 1)
    for p in PAIR_TICK
}

# Fixed distance: 200 DXY ticks = 0.200 DXY price units
FIXED_DXY_TICKS = 200
FIXED_SL_D      = FIXED_DXY_TICKS * DXY_TICK   # 0.200

# Position sizing
ACCOUNT_SIZE = 100_000
RISK_PCT     = 0.00125          # 0.125%
RISK_USD     = ACCOUNT_SIZE * RISK_PCT   # $125 per trade per pair

# Fixed pair distances (price units)
FIXED_PAIR_DIST = {p: FIXED_SL_D * PAIR_FACTOR[p] for p in PAIR_FACTOR}

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def load_pair(filepath):
    df = pd.read_csv(filepath, low_memory=False)
    df = df[['time', 'open', 'high', 'low', 'close']].copy()
    df = df.sort_values('time').reset_index(drop=True)
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)
    return df

def build_time_index(df):
    return {t: i for i, t in enumerate(df['time'])}

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def run():
    print("Loading DXY data…")
    df_raw = pd.read_csv(CSV_PATH, low_memory=False)
    df = df_raw[['time', 'open', 'high', 'low', 'close']].copy()
    df = df.sort_values('time').reset_index(drop=True)
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)
    print(f"  {len(df)} bars  |  {df['time'].iloc[0]} to {df['time'].iloc[-1]}")

    print("Loading pair data…")
    pair_dfs  = {}
    pair_tidx = {}
    for pair, fpath in PAIR_FILES.items():
        pair_dfs[pair]  = load_pair(fpath)
        pair_tidx[pair] = build_time_index(pair_dfs[pair])
        print(f"  {pair}: {len(pair_dfs[pair])} bars")

    print("Computing DXY indicators…")
    df = compute_indicators(df)
    df['bull_div'] = div_score_bull(df, DIV_LOOKBACK)
    df['bear_div'] = div_score_bear(df, DIV_LOOKBACK)
    df['bull_sig'], df['bear_sig'] = candle_patterns(df)
    sess = session_flags(df)
    df   = pd.concat([df, sess], axis=1)

    # ------------------------------------------------------------------
    # DXY SIGNAL DETECTION (identical logic to dxy_backtest.py)
    # ------------------------------------------------------------------
    print("Detecting DXY signals…")
    dxy_signals = []
    zone_top = zone_bottom = np.nan
    japan_bull = False
    zone_pristine = zone_body_clean = False
    japan_candle_cnt = 0
    zone_traded = False
    in_trade_until = -1
    n = len(df)

    for i in range(2, n):
        row = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']

        if row['is_2345']:
            zt, zb, jb = form_zone(df, i)
            if zt is not None:
                zone_top = zt; zone_bottom = zb; japan_bull = jb
                zone_pristine = True; zone_body_clean = True
                japan_candle_cnt = 0; zone_traded = False
            continue

        if np.isnan(zone_top):
            continue

        if row['in_japan']:
            japan_candle_cnt += 1
            if zone_body_clean and japan_candle_cnt > 3:
                if zone_bottom <= c <= zone_top:
                    zone_body_clean = False

        if zone_pristine:
            if japan_bull:
                if c < zone_bottom: zone_pristine = False
            else:
                if c > zone_top:    zone_pristine = False

        if zone_traded or i <= in_trade_until:
            continue

        dist_tp_long   = (zone_top    - c) * 10000
        dist_tp_short  = (c - zone_bottom) * 10000
        dist_rev_long  = abs(c - zone_bottom) * 10000
        dist_rev_short = abs(zone_top - c)    * 10000

        adx_4h = row.get('adx_4h', np.nan)
        adx_ok = (not USE_ADX_GATE) or (not np.isnan(adx_4h) and adx_4h >= ADX_MIN)

        body_pts  = abs(c - o) * 10000
        range_pts = (h - l)   * 10000
        rev_candle_ok = (body_pts >= REV_MIN_BODY) and (range_pts >= REV_MIN_RANGE)

        sig = None

        # ATTRACTION
        if (ATTR_ENABLED and zone_body_clean and zone_pristine and
                row['in_sess'] and not row['in_japan']):
            if (not japan_bull and row['bull_sig'] and
                    ATTR_MIN_PTS <= dist_tp_long <= ATTR_MAX_PTS):
                sig = dict(type='ATTR_LONG', dxy_direction=+1,
                           entry_time=row['time'], entry_price=round(c, 5))
            elif (japan_bull and row['bear_sig'] and
                    ATTR_MIN_PTS <= dist_tp_short <= ATTR_MAX_PTS):
                sig = dict(type='ATTR_SHORT', dxy_direction=-1,
                           entry_time=row['time'], entry_price=round(c, 5))

        # REVERSAL
        if sig is None and (REV_ENABLED and not zone_pristine and
                row['in_rev_sess'] and not row['in_japan'] and adx_ok and rev_candle_ok):
            bull_ok = (row['bull_sig'] and row['bull_div'] >= REV_MIN_DIV and
                       dist_rev_long <= REV_MAX_DIST)
            bear_ok = (row['bear_sig'] and row['bear_div'] >= REV_MIN_DIV and
                       dist_rev_short <= REV_MAX_DIST)
            if bull_ok:
                sig = dict(type='REV_LONG', dxy_direction=+1,
                           entry_time=row['time'], entry_price=round(c, 5))
            elif bear_ok:
                sig = dict(type='REV_SHORT', dxy_direction=-1,
                           entry_time=row['time'], entry_price=round(c, 5))

        if sig:
            # Resolve DXY trade for reference only (not used for P&L)
            min_d  = REV_MIN_SL / 10000.0
            if sig['type'] == 'ATTR_LONG':
                dxy_sl_d = zone_top - c; dxy_tp = zone_top; dxy_sl = c - dxy_sl_d
                dxy_out, dxy_exit, eb = resolve_trade(df, i, c, dxy_tp, dxy_sl, 'long')
            elif sig['type'] == 'ATTR_SHORT':
                dxy_sl_d = c - zone_bottom; dxy_tp = zone_bottom; dxy_sl = c + dxy_sl_d
                dxy_out, dxy_exit, eb = resolve_trade(df, i, c, dxy_tp, dxy_sl, 'short')
            elif sig['type'] == 'REV_LONG':
                dxy_sl_d = max(c - zone_bottom, min_d); dxy_tp = c + dxy_sl_d; dxy_sl = c - dxy_sl_d
                dxy_out, dxy_exit, eb = resolve_trade(df, i, c, dxy_tp, dxy_sl, 'long')
            else:
                dxy_sl_d = max(zone_top - c, min_d); dxy_tp = c - dxy_sl_d; dxy_sl = c + dxy_sl_d
                dxy_out, dxy_exit, eb = resolve_trade(df, i, c, dxy_tp, dxy_sl, 'short')

            sig['dxy_outcome']    = dxy_out
            sig['dxy_sl_pts']     = round(dxy_sl_d * 10000)
            sig['zone_top']       = round(zone_top, 5)
            sig['zone_bottom']    = round(zone_bottom, 5)
            dxy_signals.append(sig)
            zone_traded = True; in_trade_until = eb

    print(f"  {len(dxy_signals)} DXY signals detected\n")

    # ------------------------------------------------------------------
    # APPLY FIXED-TICK DISTANCE TO EACH PAIR
    # ------------------------------------------------------------------
    print("Applying fixed-200-tick distance to all pairs…")
    pair_results = {}

    for pair in PAIR_FILES:
        pdf      = pair_dfs[pair]
        tidx     = pair_tidx[pair]
        dir_mult = PAIR_DIRECTION[pair]
        dist     = FIXED_PAIR_DIST[pair]
        trades   = []

        for sig in dxy_signals:
            t = sig['entry_time']

            if t in tidx:
                pi = tidx[t]
            else:
                times = pdf['time'].values
                pos   = np.searchsorted(times, t, side='right') - 1
                if pos < 0 or pos >= len(pdf):
                    continue
                pi = int(pos)

            entry_px = pdf.at[pi, 'close']
            combined = sig['dxy_direction'] * dir_mult   # +1 long / -1 short

            if combined == +1:
                tp = entry_px + dist
                sl = entry_px - dist
                outcome, exit_px, _ = resolve_trade(pdf, pi, entry_px, tp, sl, 'long')
            else:
                tp = entry_px - dist
                sl = entry_px + dist
                outcome, exit_px, _ = resolve_trade(pdf, pi, entry_px, tp, sl, 'short')

            pnl_usd = RISK_USD if outcome == 'win' else (-RISK_USD if outcome == 'loss' else 0.0)

            trades.append({
                'dxy_type':    sig['type'],
                'dxy_outcome': sig['dxy_outcome'],
                'entry_time':  t,
                'pair':        pair,
                'direction':   'LONG' if combined == +1 else 'SHORT',
                'entry_price': round(entry_px, 6),
                'tp':          round(tp, 6),
                'sl':          round(sl, 6),
                'dist':        round(dist, 6),
                'outcome':     outcome,
                'exit_price':  round(exit_px, 6),
                'pnl_usd':     round(pnl_usd, 2),
            })

        pair_results[pair] = trades

    return dxy_signals, pair_results


# ---------------------------------------------------------------------------
# REPORTING
# ---------------------------------------------------------------------------

def report(dxy_signals, pair_results):
    sep = '=' * 72

    # ── DXY signals (reference) ──────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  DXY SIGNALS  (reference — win/loss NOT used for P&L)")
    print(sep)
    dxy_df = pd.DataFrame(dxy_signals)
    n  = len(dxy_df)
    w  = (dxy_df['dxy_outcome'] == 'win').sum()
    l  = (dxy_df['dxy_outcome'] == 'loss').sum()
    wr = w / n * 100 if n else 0
    pf = w / l if l else float('inf')
    print(f"  {n} signals  |  DXY WR {wr:.1f}%  |  DXY PF {pf:.3f}  |  W:{w} L:{l}")
    print(f"\n  {'Type':<12}  {'N':>3}  {'DXY WR':>7}  {'DXY SL (pts)':>14}")
    print(f"  {'-'*44}")
    for typ in dxy_df['type'].unique():
        sub  = dxy_df[dxy_df['type'] == typ]
        sw   = (sub['dxy_outcome'] == 'win').sum()
        avgsl= sub['dxy_sl_pts'].mean()
        print(f"  {typ:<12}  {len(sub):>3}  {sw/len(sub)*100:>6.0f}%  {avgsl:>14.0f}")

    # ── Per-pair summary ─────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  PAIR RESULTS  (fixed ±{FIXED_DXY_TICKS}-tick distance  |  ${RISK_USD:.0f} risk/trade)")
    print(sep)
    print(f"  {'Pair':<8}  {'N':>3}  {'WR':>7}  {'PF':>8}  {'W':>3}  {'L':>3}  {'T':>3}  "
          f"{'Net P&L':>10}  {'Vs DXY WR':>10}")
    print(f"  {'-'*66}")

    all_trades = []
    for pair in PAIR_FILES:
        trades = pair_results[pair]
        if not trades:
            print(f"  {pair:<8}  — no matching bars")
            continue
        n_p  = len(trades)
        w_p  = sum(1 for t in trades if t['outcome'] == 'win')
        l_p  = sum(1 for t in trades if t['outcome'] == 'loss')
        t_p  = sum(1 for t in trades if t['outcome'] == 'timeout')
        wr_p = w_p / n_p * 100
        pf_p = w_p / l_p if l_p else float('inf')
        net  = sum(t['pnl_usd'] for t in trades)
        all_trades.extend(trades)

        # Compare to DXY win rate on same signals
        dxy_w_same = sum(1 for t in trades if t['dxy_outcome'] == 'win')
        dxy_wr_same = dxy_w_same / n_p * 100
        diff = wr_p - dxy_wr_same

        print(f"  {pair:<8}  {n_p:>3}  {wr_p:>6.1f}%  {pf_p:>8.3f}  {w_p:>3}  {l_p:>3}  {t_p:>3}  "
              f"  ${net:>+8,.0f}  {diff:>+9.1f}pp")

    # ── Attraction vs reversal split ──────────────────────────────────────
    for sig_group, label in [('ATTR', 'ATTRACTION'), ('REV', 'REVERSAL')]:
        print(f"\n{sep}")
        print(f"  {label} TRADES per pair  (fixed ±{FIXED_DXY_TICKS} ticks)")
        print(sep)
        print(f"  {'Pair':<8}  {'N':>3}  {'WR':>7}  {'PF':>8}  {'Net P&L':>10}")
        print(f"  {'-'*44}")
        for pair in PAIR_FILES:
            sub = [t for t in pair_results[pair] if t['dxy_type'].startswith(sig_group)]
            if not sub:
                print(f"  {pair:<8}  —")
                continue
            n_s  = len(sub)
            w_s  = sum(1 for t in sub if t['outcome'] == 'win')
            l_s  = sum(1 for t in sub if t['outcome'] == 'loss')
            wr_s = w_s / n_s * 100
            pf_s = w_s / l_s if l_s else float('inf')
            net_s= sum(t['pnl_usd'] for t in sub)
            print(f"  {pair:<8}  {n_s:>3}  {wr_s:>6.1f}%  {pf_s:>8.3f}  ${net_s:>+8,.0f}")

    # ── Portfolio view ────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  PORTFOLIO  (all 8 pairs  |  ${RISK_USD:.0f} risk per trade  |  "
          f"${ACCOUNT_SIZE:,} account)")
    print(sep)
    pw = sum(1 for t in all_trades if t['outcome'] == 'win')
    pl = sum(1 for t in all_trades if t['outcome'] == 'loss')
    pt = sum(1 for t in all_trades if t['outcome'] == 'timeout')
    pn = len(all_trades)
    pwr = pw / (pw + pl) * 100 if (pw + pl) else 0
    ppf = pw / pl if pl else float('inf')
    pnet = sum(t['pnl_usd'] for t in all_trades)
    max_open = len(PAIR_FILES)
    max_risk = max_open * RISK_USD
    print(f"  Total trades     : {pn}  (W:{pw}  L:{pl}  T:{pt})")
    print(f"  Win Rate         : {pwr:.1f}%")
    print(f"  Profit Factor    : {ppf:.3f}")
    print(f"  Net P&L          : ${pnet:+,.0f}")
    print(f"  Max concurrent   : {max_open} pairs × ${RISK_USD:.0f} = ${max_risk:,.0f} per signal")
    print(f"  Return on account: {pnet/ACCOUNT_SIZE*100:+.2f}%")

    # ── Per-signal breakdown ──────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  PER-SIGNAL BREAKDOWN  (DXY outcome shown for reference)")
    print(sep)
    print(f"  {'Date/Time':<17}  {'Type':<10}  {'DXY':>4}  {'Pairs':>6}  "
          f"{'W':>4}  {'L':>4}  {'T':>4}  {'P&L':>8}")
    print(f"  {'-'*68}")
    for sig in dxy_signals:
        t    = sig['entry_time']
        styp = sig['type']
        dout = 'W' if sig['dxy_outcome'] == 'win' else ('L' if sig['dxy_outcome'] == 'loss' else 'T')
        ptrades = [tr for pair_list in pair_results.values() for tr in pair_list if tr['entry_time'] == t]
        pw_ = sum(1 for tr in ptrades if tr['outcome'] == 'win')
        pl_ = sum(1 for tr in ptrades if tr['outcome'] == 'loss')
        pt_ = sum(1 for tr in ptrades if tr['outcome'] == 'timeout')
        pnl = sum(tr['pnl_usd'] for tr in ptrades)
        print(f"  {t[:16]:<17}  {styp:<10}  DXY:{dout}  {len(ptrades):>5}  "
              f"{pw_:>4}  {pl_:>4}  {pt_:>4}  ${pnl:>+7,.0f}")

    # ── Save CSV ──────────────────────────────────────────────────────────
    rows = []
    for pair, trades in pair_results.items():
        rows.extend(trades)
    if rows:
        out = pd.DataFrame(rows)
        path = os.path.join(BASE_DIR, 'dxy_fixed_tick_trades.csv')
        out.to_csv(path, index=False)
        print(f"\nFull trade log saved: {path}")


if __name__ == '__main__':
    dxy_signals, pair_results = run()
    report(dxy_signals, pair_results)
