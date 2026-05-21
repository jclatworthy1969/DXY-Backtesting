"""
DXY Multi-Pair Backtest
=======================
Applies the DXY zone strategy signals to 8 tradeable currency pairs.

Signal detection is 100% DXY-driven (same logic as dxy_backtest.py).
When a DXY signal fires, the same TP/SL tick count is applied to each pair
using its own tick size, with XAUUSD using 10x the DXY tick count.

Conversion formula:
    pair_price_dist = dxy_sl_dist × (pair_tick_size / DXY_TICK_SIZE) × gold_mult

    DXY tick  = 0.001 (3dp CSV)
    FX pairs  = 0.00001 (5dp CSV) → factor 0.01
    USDJPY    = 0.001  (3dp CSV) → factor 1.0
    XAUUSD    = 0.01   (2dp CSV), 10x multiplier → factor 100

Direction:
    DXY Long  → LONG:  USDJPY, USDCAD, USDCHF
               SHORT: EURUSD, GBPUSD, AUDUSD, NZDUSD, XAUUSD
    DXY Short → opposite
"""

import pandas as pd
import numpy as np
import sys
import os

# ---------------------------------------------------------------------------
# Re-use all signal detection logic from dxy_backtest.py
# ---------------------------------------------------------------------------
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
# PAIR CONFIGURATION
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

# +1 = same direction as DXY (USD is base), -1 = inverse (USD is quote / gold)
PAIR_DIRECTION = {
    'EURUSD': -1, 'GBPUSD': -1, 'AUDUSD': -1, 'NZDUSD': -1,
    'USDCAD': +1, 'USDCHF': +1, 'USDJPY': +1,
    'XAUUSD': -1,
}

DXY_TICK  = 0.001   # DXY CSV is 3dp

PAIR_TICK = {
    'EURUSD': 0.00001, 'GBPUSD': 0.00001, 'AUDUSD': 0.00001, 'NZDUSD': 0.00001,
    'USDCAD': 0.00001, 'USDCHF': 0.00001,
    'USDJPY': 0.001,
    'XAUUSD': 0.01,
}

XAUUSD_MULT = 10   # gold uses 10x the DXY tick count

# All 8 pairs receive BOTH attraction AND reversal signals — no exclusions
REVERSAL_EXCLUDED_PAIRS = set()

# Pre-compute: pair_price_dist = dxy_sl_dist * PAIR_FACTOR[pair]
PAIR_FACTOR = {
    pair: (PAIR_TICK[pair] / DXY_TICK) * (XAUUSD_MULT if pair == 'XAUUSD' else 1)
    for pair in PAIR_TICK
}
# Results: FX 5dp pairs → 0.01, USDJPY → 1.0, XAUUSD → 100.0

# ---------------------------------------------------------------------------
# LOAD PAIR DATA
# ---------------------------------------------------------------------------

def load_pair(pair, filepath):
    df = pd.read_csv(filepath, low_memory=False)
    df = df[['time', 'open', 'high', 'low', 'close']].copy()
    df = df.sort_values('time').reset_index(drop=True)
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)
    return df

# ---------------------------------------------------------------------------
# BUILD TIME → INDEX LOOKUP
# ---------------------------------------------------------------------------

def build_time_index(df):
    """Return dict: timestamp_string → integer row index."""
    return {t: i for i, t in enumerate(df['time'])}

# ---------------------------------------------------------------------------
# MAIN MULTI-PAIR BACKTEST
# ---------------------------------------------------------------------------

def run_multi_pair_backtest():
    print("Loading DXY data…")
    df_raw = pd.read_csv(CSV_PATH, low_memory=False)
    df = df_raw[['time', 'open', 'high', 'low', 'close']].copy()
    df = df.sort_values('time').reset_index(drop=True)
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)
    print(f"DXY bars: {len(df)}  |  {df['time'].iloc[0]} to {df['time'].iloc[-1]}")

    print("Loading pair data…")
    pair_dfs   = {}
    pair_tidx  = {}
    for pair, fpath in PAIR_FILES.items():
        pair_dfs[pair]  = load_pair(pair, fpath)
        pair_tidx[pair] = build_time_index(pair_dfs[pair])
        print(f"  {pair}: {len(pair_dfs[pair])} bars")

    print("Computing DXY indicators…")
    df = compute_indicators(df)
    df['bull_div'] = div_score_bull(df, DIV_LOOKBACK)
    df['bear_div'] = div_score_bear(df, DIV_LOOKBACK)
    df['bull_sig'], df['bear_sig'] = candle_patterns(df)
    sess = session_flags(df)
    df   = pd.concat([df, sess], axis=1)

    # -- DXY Strategy Loop (identical to dxy_backtest.py) --------------------
    dxy_trades = []  # each entry also carries sl_d (raw dist) and dxy_direction

    zone_top         = np.nan
    zone_bottom      = np.nan
    japan_bull       = False
    zone_pristine    = False
    zone_body_clean  = False
    japan_candle_cnt = 0
    zone_traded      = False
    in_trade_until   = -1

    print("Running DXY signal detection…")
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

        # Trend gate (4H)
        trend_buf   = 30 / 10000.0
        _4h_close   = row.get('adx_4h', np.nan)  # placeholder — trend_ok computed below

        entry = None  # will be set if a signal fires

        # --- ATTRACTION -------------------------------------------------------
        if (ATTR_ENABLED and zone_body_clean and zone_pristine and
                row['in_sess'] and not row['in_japan']):

            if (not japan_bull and row['bull_sig'] and
                    ATTR_MIN_PTS <= dist_tp_long <= ATTR_MAX_PTS):
                sl_d  = zone_top - c
                tp_d  = sl_d            # 1:1 RR
                tp    = zone_top
                sl    = c - sl_d
                outcome, exit_px, exit_bar = resolve_trade(df, i, c, tp, sl, 'long')
                entry = dict(
                    type='ATTR_LONG', dxy_direction=+1,
                    entry_time=row['time'], entry_price=round(c, 5),
                    tp=round(tp, 5), sl=round(sl, 5),
                    sl_d=sl_d, tp_d=tp_d,
                    sl_pts=round(sl_d * 10000),
                    zone_top=round(zone_top, 5), zone_bottom=round(zone_bottom, 5),
                    outcome=outcome, exit_price=round(exit_px, 5),
                    pnl_pts=round((exit_px - c)*10000 if outcome=='win' else (c-exit_px)*10000*-1),
                )
                zone_traded = True; in_trade_until = exit_bar
                dxy_trades.append(entry); continue

            if (japan_bull and row['bear_sig'] and
                    ATTR_MIN_PTS <= dist_tp_short <= ATTR_MAX_PTS):
                sl_d  = c - zone_bottom
                tp_d  = sl_d
                tp    = zone_bottom
                sl    = c + sl_d
                outcome, exit_px, exit_bar = resolve_trade(df, i, c, tp, sl, 'short')
                entry = dict(
                    type='ATTR_SHORT', dxy_direction=-1,
                    entry_time=row['time'], entry_price=round(c, 5),
                    tp=round(tp, 5), sl=round(sl, 5),
                    sl_d=sl_d, tp_d=tp_d,
                    sl_pts=round(sl_d * 10000),
                    zone_top=round(zone_top, 5), zone_bottom=round(zone_bottom, 5),
                    outcome=outcome, exit_price=round(exit_px, 5),
                    pnl_pts=round((c-exit_px)*10000 if outcome=='win' else (exit_px-c)*10000*-1),
                )
                zone_traded = True; in_trade_until = exit_bar
                dxy_trades.append(entry); continue

        # --- REVERSAL ---------------------------------------------------------
        if (REV_ENABLED and not zone_pristine and
                row['in_rev_sess'] and not row['in_japan'] and adx_ok and rev_candle_ok):

            bull_ok = (row['bull_sig'] and row['bull_div'] >= REV_MIN_DIV and
                       dist_rev_long <= REV_MAX_DIST)
            bear_ok = (row['bear_sig'] and row['bear_div'] >= REV_MIN_DIV and
                       dist_rev_short <= REV_MAX_DIST)

            if bull_ok:
                min_d = REV_MIN_SL / 10000.0
                sl_d  = max(c - zone_bottom, min_d)
                tp    = c + sl_d; sl = c - sl_d
                outcome, exit_px, exit_bar = resolve_trade(df, i, c, tp, sl, 'long')
                entry = dict(
                    type='REV_LONG', dxy_direction=+1,
                    entry_time=row['time'], entry_price=round(c, 5),
                    tp=round(tp, 5), sl=round(sl, 5),
                    sl_d=sl_d, tp_d=sl_d,
                    sl_pts=round(sl_d * 10000),
                    zone_top=round(zone_top, 5), zone_bottom=round(zone_bottom, 5),
                    outcome=outcome, exit_price=round(exit_px, 5),
                    pnl_pts=round((exit_px-c)*10000 if outcome=='win' else (c-exit_px)*10000*-1),
                )
                zone_traded = True; in_trade_until = exit_bar
                dxy_trades.append(entry); continue

            if bear_ok:
                min_d = REV_MIN_SL / 10000.0
                sl_d  = max(zone_top - c, min_d)
                tp    = c - sl_d; sl = c + sl_d
                outcome, exit_px, exit_bar = resolve_trade(df, i, c, tp, sl, 'short')
                entry = dict(
                    type='REV_SHORT', dxy_direction=-1,
                    entry_time=row['time'], entry_price=round(c, 5),
                    tp=round(tp, 5), sl=round(sl, 5),
                    sl_d=sl_d, tp_d=sl_d,
                    sl_pts=round(sl_d * 10000),
                    zone_top=round(zone_top, 5), zone_bottom=round(zone_bottom, 5),
                    outcome=outcome, exit_price=round(exit_px, 5),
                    pnl_pts=round((c-exit_px)*10000 if outcome=='win' else (exit_px-c)*10000*-1),
                )
                zone_traded = True; in_trade_until = exit_bar
                dxy_trades.append(entry)

    print(f"\nDXY signals detected: {len(dxy_trades)}")

    # -- Apply signals to each pair -----------------------------------------
    print("\nApplying DXY signals to currency pairs…")

    pair_results = {}   # pair → list of trade dicts

    for pair in PAIR_FILES:
        pdf      = pair_dfs[pair]
        tidx     = pair_tidx[pair]
        factor   = PAIR_FACTOR[pair]
        dir_mult = PAIR_DIRECTION[pair]
        pair_trades = []

        for sig in dxy_trades:
            t = sig['entry_time']

            # Skip reversal signals for excluded pairs
            if sig['type'].startswith('REV') and pair in REVERSAL_EXCLUDED_PAIRS:
                continue

            # Find matching bar index in pair data
            if t in tidx:
                pi = tidx[t]
            else:
                # Fall back to nearest prior bar
                times = pdf['time'].values
                pos   = np.searchsorted(times, t, side='right') - 1
                if pos < 0 or pos >= len(pdf):
                    continue
                pi = int(pos)

            pair_entry = pdf.at[pi, 'close']
            sl_d       = sig['sl_d']     # raw DXY price distance
            pair_dist  = sl_d * factor   # converted to pair price distance

            # Direction: dxy_direction (+1 long / -1 short) × pair_direction (+1 direct / -1 inverse)
            combined = sig['dxy_direction'] * dir_mult

            if combined == +1:   # LONG on this pair
                tp = pair_entry + pair_dist
                sl = pair_entry - pair_dist
                outcome, exit_px, exit_bar = resolve_trade(pdf, pi, pair_entry, tp, sl, 'long')
                pnl = (exit_px - pair_entry) if outcome == 'win' else -(pair_entry - exit_px) * -1
                pnl_normalized = pnl / pair_dist  # +1.0 = win, -1.0 = loss (1:1 RR)
            else:                # SHORT on this pair
                tp = pair_entry - pair_dist
                sl = pair_entry + pair_dist
                outcome, exit_px, exit_bar = resolve_trade(pdf, pi, pair_entry, tp, sl, 'short')
                pnl = (pair_entry - exit_px) if outcome == 'win' else -(exit_px - pair_entry) * -1
                pnl_normalized = pnl / pair_dist

            pair_trades.append({
                'dxy_type':    sig['type'],
                'entry_time':  t,
                'pair_entry':  round(pair_entry, 6),
                'tp':          round(tp, 6),
                'sl':          round(sl, 6),
                'dist':        round(pair_dist, 6),
                'direction':   'LONG' if combined == +1 else 'SHORT',
                'outcome':     outcome,
                'exit_price':  round(exit_px, 6),
            })

        pair_results[pair] = pair_trades

    return dxy_trades, pair_results

# ---------------------------------------------------------------------------
# REPORTING
# ---------------------------------------------------------------------------

def report(dxy_trades, pair_results):
    def pair_stats(trades, label):
        if not trades:
            print(f"  {label:<8}: No trades")
            return
        n   = len(trades)
        w   = sum(1 for t in trades if t['outcome'] == 'win')
        l   = sum(1 for t in trades if t['outcome'] == 'loss')
        to  = sum(1 for t in trades if t['outcome'] == 'timeout')
        wr  = w / n * 100
        pf  = w / l if l > 0 else float('inf')
        print(f"  {label:<8}: {n:2d} trades  |  WR {wr:5.1f}%  |  PF {pf:6.3f}  "
              f"|  W:{w} L:{l} T:{to}")

    # -- DXY summary --------------------------------------------------------
    dxy_df = pd.DataFrame(dxy_trades)
    n   = len(dxy_df)
    w   = len(dxy_df[dxy_df['outcome'] == 'win'])
    l   = len(dxy_df[dxy_df['outcome'] == 'loss'])
    wr  = w / n * 100 if n > 0 else 0
    gw  = dxy_df[dxy_df['outcome'] == 'win']['sl_pts'].sum()
    gl  = dxy_df[dxy_df['outcome'] == 'loss']['sl_pts'].sum()
    pf  = gw / gl if gl > 0 else float('inf')

    print(f"\n{'='*70}")
    print(f"  DXY SIGNALS (source)")
    print(f"{'='*70}")
    print(f"  {n} trades  |  WR {wr:.1f}%  |  PF {pf:.3f}  |  W:{w} L:{l}")
    for typ in dxy_df['type'].unique():
        sub = dxy_df[dxy_df['type'] == typ]
        sw  = len(sub[sub['outcome'] == 'win'])
        print(f"    {typ:<12}: {len(sub):2d} trades  |  WR {sw/len(sub)*100:.0f}%")

    # -- Per-pair results ---------------------------------------------------
    print(f"\n{'='*70}")
    print(f"  PAIR RESULTS  (same {n} DXY signals applied to each pair)")
    print(f"{'='*70}")
    print(f"  {'Pair':<8}  {'Trades':>6}  {'Win Rate':>9}  {'Prof Factor':>12}  {'W':>4}  {'L':>4}  {'T':>4}")
    print(f"  {'-'*62}")

    for pair in PAIR_FILES:
        trades = pair_results[pair]
        if not trades:
            print(f"  {pair:<8}: no matching bars")
            continue
        n_p = len(trades)
        w_p = sum(1 for t in trades if t['outcome'] == 'win')
        l_p = sum(1 for t in trades if t['outcome'] == 'loss')
        t_p = sum(1 for t in trades if t['outcome'] == 'timeout')
        wr_p = w_p / n_p * 100
        pf_p = w_p / l_p if l_p > 0 else float('inf')
        print(f"  {pair:<8}  {n_p:>6}  {wr_p:>8.1f}%  {pf_p:>12.3f}  {w_p:>4}  {l_p:>4}  {t_p:>4}")

    # -- Breakdown: attraction vs reversal per pair --------------------------
    print(f"\n{'='*70}")
    print(f"  ATTRACTION TRADES per pair")
    print(f"{'='*70}")
    print(f"  {'Pair':<8}  {'Trades':>6}  {'Win Rate':>9}  {'Prof Factor':>12}")
    print(f"  {'-'*50}")
    for pair in PAIR_FILES:
        trades = [t for t in pair_results[pair] if t['dxy_type'].startswith('ATTR')]
        if not trades: print(f"  {pair:<8}: -"); continue
        n_p = len(trades); w_p = sum(1 for t in trades if t['outcome']=='win')
        l_p = sum(1 for t in trades if t['outcome']=='loss')
        wr_p = w_p/n_p*100; pf_p = w_p/l_p if l_p > 0 else float('inf')
        print(f"  {pair:<8}  {n_p:>6}  {wr_p:>8.1f}%  {pf_p:>12.3f}")

    print(f"\n{'='*70}")
    print(f"  REVERSAL TRADES per pair")
    print(f"{'='*70}")
    print(f"  {'Pair':<8}  {'Trades':>6}  {'Win Rate':>9}  {'Prof Factor':>12}")
    print(f"  {'-'*50}")
    for pair in PAIR_FILES:
        trades = [t for t in pair_results[pair] if t['dxy_type'].startswith('REV')]
        if not trades: print(f"  {pair:<8}: -"); continue
        n_p = len(trades); w_p = sum(1 for t in trades if t['outcome']=='win')
        l_p = sum(1 for t in trades if t['outcome']=='loss')
        wr_p = w_p/n_p*100; pf_p = w_p/l_p if l_p > 0 else float('inf')
        print(f"  {pair:<8}  {n_p:>6}  {wr_p:>8.1f}%  {pf_p:>12.3f}")

    # -- Portfolio view: all pairs treated as equal risk units ---------------
    print(f"\n{'='*70}")
    print(f"  PORTFOLIO VIEW  (equal 1-unit risk per pair per signal)")
    print(f"  All 8 pairs receive both attraction AND reversal signals")
    print(f"{'='*70}")

    port_wins = port_losses = port_timeouts = 0
    port_gross_w = port_gross_l = 0.0

    for pair, trades in pair_results.items():
        for t in trades:
            if t['outcome'] == 'win':
                port_wins    += 1
                port_gross_w += 1.0
            elif t['outcome'] == 'loss':
                port_losses  += 1
                port_gross_l += 1.0
            else:
                port_timeouts += 1

    port_total = port_wins + port_losses + port_timeouts
    port_wr    = port_wins / (port_wins + port_losses) * 100 if (port_wins + port_losses) > 0 else 0
    port_pf    = port_gross_w / port_gross_l if port_gross_l > 0 else float('inf')
    port_net   = port_gross_w - port_gross_l

    print(f"  Total pair-trades : {port_total}  (W: {port_wins}  L: {port_losses}  T: {port_timeouts})")
    print(f"  Win Rate          : {port_wr:.1f}%")
    print(f"  Profit Factor     : {port_pf:.3f}")
    print(f"  Net units         : {port_net:+.0f}  (each unit = 1R)")

    # Per-signal portfolio breakdown
    print(f"\n  Per DXY signal — combined pair outcomes:")
    print(f"  {'Signal':<26}  {'Pairs traded':>12}  {'Wins':>5}  {'Losses':>7}  {'Net':>6}")
    print(f"  {'-'*60}")
    for sig in dxy_trades:
        t       = sig['entry_time']
        sig_typ = sig['type']
        sig_out = sig['outcome']
        p_trades = [tr for pair_list in pair_results.values()
                    for tr in pair_list if tr['entry_time'] == t]
        p_w = sum(1 for tr in p_trades if tr['outcome'] == 'win')
        p_l = sum(1 for tr in p_trades if tr['outcome'] == 'loss')
        p_n = len(p_trades)
        net = p_w - p_l
        dxy_mark = 'W' if sig_out == 'win' else 'L'
        print(f"  {t[:16]}  {sig_typ:<10} DXY:{dxy_mark}  {p_n:>5} pairs  {p_w:>5}W  {p_l:>7}L  {net:>+6}")

    # -- Save full multi-pair trade log to CSV --------------------------------
    rows = []
    for pair, trades in pair_results.items():
        for t in trades:
            t['pair'] = pair
            rows.append(t)
    if rows:
        out_df   = pd.DataFrame(rows)
        out_path = os.path.join(BASE_DIR, 'dxy_multi_pair_trades.csv')
        out_df.to_csv(out_path, index=False)
        print(f"\nFull multi-pair trade log saved: {out_path}")


if __name__ == '__main__':
    dxy_trades, pair_results = run_multi_pair_backtest()
    report(dxy_trades, pair_results)
