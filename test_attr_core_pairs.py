"""
test_attr_core_pairs.py
=======================
Tests the ATTR signal with CORE filter applied against all 6 active pairs
using DXY-exit logic.

CORE filter (derived from wave analysis of 328 pristine setups, Sep 23 - May 26):
  gap_pts_at_lon  < 1500 pts  -- zone is close at London open
  wave_extension  < 1500 pts  -- DXY didn't run further away during London session

Active pairs (GBPUSD and USDCHF dropped as consistent losers):
  EURUSD, AUDUSD, NZDUSD, USDCAD, USDJPY, XAUUSD

Exit method: DXY-exit — pair trade closed when DXY hits its own TP or SL bar.
Results expressed as fractional R.
"""

import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats as scipy_stats
from dxy_clean_rules import load_news_filter as load_news_legacy

# ── Config ────────────────────────────────────────────────────────────────────
BASE = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

# Use merged (full-history) files where available, shorter for AUDUSD/NZDUSD
FILE_MAP = {
    'DXY'   : BASE / 'TVC_DXY, 15_merged.csv',
    'EURUSD': BASE / 'FX_EURUSD, 15_merged.csv',
    'AUDUSD': BASE / 'FX_AUDUSD, 15 (1).csv',
    'NZDUSD': BASE / 'FX_NZDUSD, 15 (1).csv',
    'USDCAD': BASE / 'FX_USDCAD, 15_merged.csv',
    'USDJPY': BASE / 'FX_USDJPY, 15_merged.csv',
    'XAUUSD': BASE / 'FX_XAUUSD, 15_merged.csv',
}

# Pair direction vs DXY (+1 = same direction, -1 = inverse)
PAIR_DIR = {
    'EURUSD': -1,
    'AUDUSD': -1,
    'NZDUSD': -1,
    'USDCAD': +1,
    'USDJPY': +1,
    'XAUUSD': -1,
}

# DXY pts → pair price conversion factor
# pair_price_move = dxy_pts / 10000 * FACTOR
PAIR_FACTOR = {
    'EURUSD': 0.01,
    'AUDUSD': 0.01,
    'NZDUSD': 0.01,
    'USDCAD': 0.01,
    'USDJPY': 1.0,
    'XAUUSD': 100.0,
}

PAIRS = list(PAIR_DIR.keys())

# CORE filter thresholds
CORE_GAP_MAX       = 1500   # pts — zone must be within 1500 pts of London open price
CORE_WAVE_EXT_MAX  = 1500   # pts — DXY must not have extended >1500 pts further from zone

# Signal parameters (inherited from dxy_clean_rules.py)
ZONE_MIN_GAP       = 30
ZONE_MIN_WIDTH     = 150
ATTR_MIN_GAP       = 75
ATTR_APPROACH_PTS  = 150
ATTR_MIN_REWARD    = 100
ATTR_WINDOW        = (7*60+30, 19*60+30)
EMA_FAST, EMA_SLOW = 20, 50
PIN_WICK_MULT      = 2.0
MAX_LOOKFORWARD    = 400


# ── Data loading ──────────────────────────────────────────────────────────────
def load(sym):
    df = pd.read_csv(FILE_MAP[sym])
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    return df[['time', 'open', 'high', 'low', 'close']].copy()


# ── News filter ───────────────────────────────────────────────────────────────
NEWS_CURRENCY_PAIRS = {
    'USD': None,
    'EUR': {'EURUSD'},
    'JPY': {'USDJPY'},
    'CAD': {'USDCAD'},
    'AUD': {'AUDUSD'},
    'NZD': {'NZDUSD'},
}

def load_news():
    return load_news_legacy()

def news_blocks(news_dates, ts_str, pair):
    if not news_dates:
        return False
    iso = ts_str[:10]
    currencies = news_dates.get(iso)
    if not currencies:
        return False
    if pair == 'ALL_USD':
        return 'USD' in currencies
    for cur, blocked in NEWS_CURRENCY_PAIRS.items():
        if cur not in currencies:
            continue
        if blocked is None:
            return True
        if pair in blocked:
            return True
    return False


# ── Indicator helpers ─────────────────────────────────────────────────────────
def compute_htf_bias(df, tf_hours):
    idx = pd.to_datetime(df['time'], utc=True)
    dt  = df.set_index(idx)
    htf = dt[['open','high','low','close']].resample(f'{tf_hours}h').agg(
          {'open':'first','high':'max','low':'min','close':'last'}).dropna()
    htf['ef'] = htf['close'].ewm(span=EMA_FAST, adjust=False).mean()
    htf['es'] = htf['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    htf['bias'] = np.where(htf['ef'] > htf['es'], 1,
                  np.where(htf['ef'] < htf['es'], -1, 0))
    def fl(ts): return int(ts.timestamp() // (tf_hours*3600)) * (tf_hours*3600)
    bmap = {fl(ts): int(row['bias']) for ts, row in htf.iterrows()}
    return pd.Series([bmap.get(fl(t), 0) for t in idx], index=df.index)


def candle_signals(df):
    c, o, h, l = df['close'], df['open'], df['high'], df['low']
    body    = (c - o).abs()
    hi_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lo_wick = pd.concat([o, c], axis=1).min(axis=1) - l
    rng     = h - l
    bull_engulf = ((c > o) & ~(c.shift(1) > o.shift(1)) &
                   (c > o.shift(1)) & (o < c.shift(1)) &
                   (body >= body.shift(1) * 0.8))
    bear_engulf = ((c < o) & ~(c.shift(1) < o.shift(1)) &
                   (c < o.shift(1)) & (o > c.shift(1)) &
                   (body >= body.shift(1) * 0.8))
    bull_pin = (lo_wick >= body * PIN_WICK_MULT) & (hi_wick <= body * 1.5) & (rng > 0)
    bear_pin = (hi_wick >= body * PIN_WICK_MULT) & (lo_wick <= body * 1.5) & (rng > 0)
    bar2r    = (c.shift(2) - o.shift(2)).abs()
    indecsn  = body.shift(1) <= bar2r * 0.5
    bull_3b  = (c.shift(2) < o.shift(2)) & indecsn & (c > o) & (c > o.shift(2))
    bear_3b  = (c.shift(2) > o.shift(2)) & indecsn & (c < o) & (c < o.shift(2))
    bull = (bull_engulf | bull_pin | bull_3b).fillna(False)
    bear = (bear_engulf | bear_pin | bear_3b).fillna(False)
    return bull, bear


def form_zone(df, i):
    if i < 1:
        return None, None, None
    prev_body = abs(df.at[i-1,'close'] - df.at[i-1,'open']) * 10000
    prior_c   = df.at[i-2,'close'] if (prev_body < 10 and i >= 2) else df.at[i-1,'close']
    j_o, j_c  = df.at[i,'open'], df.at[i,'close']
    gap       = abs(prior_c - j_o) * 10000
    if gap >= ZONE_MIN_GAP:
        zt, zb = max(prior_c, j_o), min(prior_c, j_o)
        bull   = j_o > prior_c
    else:
        zt, zb = max(j_o, j_c), min(j_o, j_c)
        bull   = j_c > j_o
    if abs(zt - zb) * 10000 < 1:
        zt = max(j_o, j_c) + 0.001
        zb = min(j_o, j_c)
    return zt, zb, bull


def resolve(df, entry_idx, entry, tp, sl, direction):
    n = len(df)
    for j in range(entry_idx + 1, min(entry_idx + MAX_LOOKFORWARD, n)):
        h_j, l_j, o_j = df.at[j,'high'], df.at[j,'low'], df.at[j,'open']
        if direction == 'long':
            if o_j <= sl: return 'loss', sl, j
            if h_j >= tp and l_j <= sl:
                return ('win' if abs(o_j-sl)>abs(tp-o_j) else 'loss'), \
                       (tp if abs(o_j-sl)>abs(tp-o_j) else sl), j
            if h_j >= tp: return 'win', tp, j
            if l_j <= sl: return 'loss', sl, j
        else:
            if o_j >= sl: return 'loss', sl, j
            if l_j <= tp and h_j >= sl:
                return ('win' if abs(o_j-sl)>abs(o_j-tp) else 'loss'), \
                       (tp if abs(o_j-sl)>abs(o_j-tp) else sl), j
            if l_j <= tp: return 'win', tp, j
            if h_j >= sl: return 'loss', sl, j
    j_last = min(entry_idx + MAX_LOOKFORWARD - 1, n-1)
    return 'timeout', df.at[j_last,'close'], j_last


# ── Signal generator (ATTR only, with CORE filter metrics) ───────────────────
def generate_attr_signals(df_dxy, news_dates=None, apply_core_filter=True):
    """
    Generates DXY ATTR signals with CORE filter applied.
    Adds gap_pts_at_lon and wave_extension_pts to each signal dict.
    """
    df = df_dxy.copy().reset_index(drop=True)
    bull_sig, bear_sig = candle_signals(df)

    zone_top     = np.nan
    zone_bottom  = np.nan
    japan_bull   = False
    attr_pristine  = False
    strict_pristine = False
    attr_traded    = False
    in_trade_until = -1

    lon_open_close  = np.nan   # DXY close at London open bar
    gap_pts_at_lon  = 0.0      # gap from zone near edge at London open

    signals = []
    n = len(df)

    for i in range(2, n):
        row  = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']
        ts   = row['time']
        hour, minute = ts.hour, ts.minute
        curr_min = hour * 60 + minute
        dow  = ts.dayofweek

        is_2345  = (hour == 23) and (minute == 45)
        in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

        # Zone formation at 23:45 UTC
        if is_2345:
            zt, zb, jb = form_zone(df, i)
            if zt is not None:
                zone_top, zone_bottom = zt, zb
                japan_bull      = jb
                attr_pristine   = False
                strict_pristine = True
                attr_traded     = False
                lon_open_close  = np.nan
                gap_pts_at_lon  = 0.0
            continue

        if np.isnan(zone_top):
            continue

        # Update strict_pristine
        if strict_pristine and (l <= zone_top) and (h >= zone_bottom):
            strict_pristine = False

        # Evaluate attr_pristine and record London open metrics
        mon_start = 6 * 60 + 30
        eff_attr_start = mon_start if dow == 0 else ATTR_WINDOW[0]
        london_open_bar = (not in_japan and
                           ((dow != 0 and curr_min == ATTR_WINDOW[0]) or
                            (dow == 0 and curr_min == mon_start)))

        if london_open_bar:
            if not japan_bull:
                gap = (zone_bottom - c) * 10000
                attr_pristine = gap >= ATTR_MIN_GAP
            else:
                gap = (c - zone_top) * 10000
                attr_pristine = gap >= ATTR_MIN_GAP
            if attr_pristine:
                lon_open_close = c
                gap_pts_at_lon = gap

        if i <= in_trade_until:
            continue

        in_attr_sess = eff_attr_start <= curr_min <= ATTR_WINDOW[1] and not in_japan

        if news_dates and news_blocks(news_dates, str(ts), 'ALL_USD'):
            continue

        zone_width_pts = (zone_top - zone_bottom) * 10000

        if i >= 3:
            c_prev3 = df.at[i-3, 'close']
            approach_pts = ((c - c_prev3) * 10000 if not japan_bull
                            else (c_prev3 - c) * 10000)
        else:
            approach_pts = 0

        impulsive_approach = approach_pts >= ATTR_APPROACH_PTS

        if (attr_pristine and in_attr_sess and
                zone_width_pts >= ZONE_MIN_WIDTH and not attr_traded):

            reward_long  = (zone_top    - c) * 10000
            reward_short = (c - zone_bottom) * 10000

            # ── CORE filter: compute wave_extension at this bar ──────────────
            if not np.isnan(lon_open_close):
                if not japan_bull:
                    # LONG setup: wave goes DOWN (price fell from lon_open_close)
                    wave_ext = (lon_open_close - c) * 10000
                else:
                    # SHORT setup: wave goes UP (price rose from lon_open_close)
                    wave_ext = (c - lon_open_close) * 10000
                wave_ext = max(wave_ext, 0.0)
            else:
                wave_ext = 0.0

            core_ok = (gap_pts_at_lon < CORE_GAP_MAX and wave_ext < CORE_WAVE_EXT_MAX)
            if apply_core_filter and not core_ok:
                # Still mark as attr_traded so we don't get a second attempt on same zone
                # (commented out: let filtered setup also block second attempts)
                pass  # don't set attr_traded — allow next bar another chance
            else:
                # ATTR LONG
                if (not japan_bull and bull_sig.at[i] and impulsive_approach
                        and reward_long >= ATTR_MIN_REWARD):
                    tp_price = zone_top
                    sl_d     = tp_price - c
                    sl_price = c - sl_d
                    if sl_d > 0:
                        outcome, exit_px, exit_bar = resolve(df, i, c, tp_price, sl_price, 'long')
                        signals.append({
                            'type'            : 'ATTR_LONG',
                            'entry_time'      : str(ts),
                            'entry'           : round(c, 5),
                            'tp'              : round(tp_price, 5),
                            'sl'              : round(sl_price, 5),
                            'sl_pts'          : round(sl_d * 10000),
                            'tp_pts'          : round(sl_d * 10000),
                            'zone_top'        : round(zone_top, 5),
                            'zone_bottom'     : round(zone_bottom, 5),
                            'zone_width'      : round(zone_width_pts),
                            'gap_pts_at_lon'  : round(gap_pts_at_lon, 1),
                            'wave_ext_pts'    : round(wave_ext, 1),
                            'core_filter'     : core_ok,
                            'outcome'         : outcome,
                            'exit_px'         : round(exit_px, 5),
                            'exit_time'       : str(df.at[exit_bar, 'time']),
                        })
                        attr_traded = True
                        in_trade_until = exit_bar
                    continue

                # ATTR SHORT
                if (japan_bull and bear_sig.at[i] and impulsive_approach
                        and reward_short >= ATTR_MIN_REWARD):
                    tp_price = zone_bottom
                    sl_d     = c - tp_price
                    sl_price = c + sl_d
                    if sl_d > 0:
                        outcome, exit_px, exit_bar = resolve(df, i, c, tp_price, sl_price, 'short')
                        signals.append({
                            'type'            : 'ATTR_SHORT',
                            'entry_time'      : str(ts),
                            'entry'           : round(c, 5),
                            'tp'              : round(tp_price, 5),
                            'sl'              : round(sl_price, 5),
                            'sl_pts'          : round(sl_d * 10000),
                            'tp_pts'          : round(sl_d * 10000),
                            'zone_top'        : round(zone_top, 5),
                            'zone_bottom'     : round(zone_bottom, 5),
                            'zone_width'      : round(zone_width_pts),
                            'gap_pts_at_lon'  : round(gap_pts_at_lon, 1),
                            'wave_ext_pts'    : round(wave_ext, 1),
                            'core_filter'     : core_ok,
                            'outcome'         : outcome,
                            'exit_px'         : round(exit_px, 5),
                            'exit_time'       : str(df.at[exit_bar, 'time']),
                        })
                        attr_traded = True
                        in_trade_until = exit_bar
                    continue

    return signals


# ── Pair trade applicator (DXY-exit) ─────────────────────────────────────────
def apply_dxy_exit(dxy_signals, df_pair, pair, news_dates=None):
    F = PAIR_FACTOR[pair]
    D = PAIR_DIR[pair]
    pair_idx = {str(t): i for i, t in enumerate(df_pair['time'])}

    results = []
    for sig in dxy_signals:
        et = sig['entry_time']
        xt = sig.get('exit_time')
        if et not in pair_idx or not xt or xt not in pair_idx:
            continue
        if news_dates and news_blocks(news_dates, et, pair):
            continue
        pi = pair_idx[et]
        xi = pair_idx[xt]
        pc = df_pair.at[pi, 'close']
        px = df_pair.at[xi, 'close']

        is_long_dxy = 'LONG' in sig['type']
        pair_long   = (is_long_dxy and D == 1) or (not is_long_dxy and D == -1)
        pair_sl_dist = sig['sl_pts'] / 10000 * F
        raw_pnl = (px - pc) if pair_long else (pc - px)
        r_actual = raw_pnl / pair_sl_dist if pair_sl_dist > 0 else 0.0
        outcome  = 'win' if r_actual > 0 else ('loss' if r_actual < 0 else 'even')

        results.append({
            'dxy_type'      : sig['type'],
            'entry_time'    : et,
            'exit_time'     : xt,
            'dxy_outcome'   : sig['outcome'],
            'pair'          : pair,
            'direction'     : 'long' if pair_long else 'short',
            'entry'         : round(pc, 5),
            'exit_px'       : round(px, 5),
            'sl_pts_dxy'    : sig['sl_pts'],
            'outcome'       : outcome,
            'r_actual'      : round(r_actual, 3),
            'gap_pts_at_lon': sig['gap_pts_at_lon'],
            'wave_ext_pts'  : sig['wave_ext_pts'],
        })
    return results


# ── Reporting ─────────────────────────────────────────────────────────────────
def stats_r(trades):
    if not trades:
        return dict(N=0, W=0, L=0, WR=0.0, PF=0.0, NetR=0.0, AvgW=0.0, AvgL=0.0)
    tdf  = pd.DataFrame(trades)
    wins = tdf[tdf['r_actual'] > 0]
    loss = tdf[tdf['r_actual'] < 0]
    w, l = len(wins), len(loss)
    wr   = w / (w + l) * 100 if (w + l) > 0 else 0
    gw   = wins['r_actual'].sum()
    gl   = loss['r_actual'].abs().sum()
    pf   = gw / gl if gl > 0 else float('inf')
    net  = round(tdf['r_actual'].sum(), 2)
    avg_w = round(gw / w, 3) if w > 0 else 0
    avg_l = round(gl / l, 3) if l > 0 else 0
    return dict(N=len(tdf), W=w, L=l,
                WR=round(wr, 1), PF=round(pf, 3), NetR=net,
                AvgW=avg_w, AvgL=avg_l)


def print_table_row(label, s, indent=2):
    sp = ' ' * indent
    if s['N'] == 0:
        print(f"{sp}{label:<12}: no trades")
        return
    pf  = f"{s['PF']:.3f}" if s['PF'] != float('inf') else "  inf"
    print(f"{sp}{label:<12}  N={s['N']:>4}  W={s['W']:>4} L={s['L']:>4}  "
          f"WR={s['WR']:>5.1f}%  PF={pf:<6}  NetR={s['NetR']:>+8.2f}R  "
          f"avgW={s['AvgW']:>+.3f}R  avgL={s['AvgL']:>-.3f}R")


def monthly_summary(all_trades):
    if not all_trades:
        return
    tdf = pd.DataFrame(all_trades)
    tdf['month'] = pd.to_datetime(tdf['entry_time']).dt.to_period('M')
    print(f"\n  {'Month':<10}  {'Trades':>7}  {'Wins':>6}  {'Net R':>8}  Cumulative R")
    print(f"  {'-'*52}")
    cumulative = 0.0
    for ym, grp in tdf.groupby('month'):
        trades_m = len(grp)
        wins_m   = (grp['r_actual'] > 0).sum()
        net_m    = grp['r_actual'].sum()
        cumulative += net_m
        print(f"  {str(ym):<10}  {trades_m:>7}  {wins_m:>6}  {net_m:>+8.2f}R  {cumulative:>+.2f}R")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Loading DXY data (merged, Sep 2023 - May 2026)...")
    df_dxy = load('DXY')
    print(f"  {len(df_dxy):,} bars  |  {df_dxy['time'].min().date()} to {df_dxy['time'].max().date()}")

    print("Loading pair data...")
    pair_dfs = {}
    for pair in PAIRS:
        pair_dfs[pair] = load(pair)
        print(f"  {pair:<8}: {len(pair_dfs[pair]):,} bars  "
              f"({pair_dfs[pair]['time'].min().date()} to {pair_dfs[pair]['time'].max().date()})")

    news_dates = load_news()
    n_usd = sum(1 for v in news_dates.values() if 'USD' in v)
    print(f"\nNews filter: {len(news_dates)} event dates loaded  ({n_usd} USD days)")

    # ── Step 1: All ATTR signals (no core filter) ────────────────────────────
    print("\nGenerating ALL ATTR signals (no filter)...")
    sigs_all = generate_attr_signals(df_dxy, news_dates=news_dates, apply_core_filter=False)
    sigs_all = [s for s in sigs_all if s['type'].startswith('ATTR')]
    print(f"  {len(sigs_all)} ATTR signals total")

    # ── Step 2: CORE-filtered ATTR signals ──────────────────────────────────
    print(f"\nApplying CORE filter  (gap < {CORE_GAP_MAX} pts  AND  wave_ext < {CORE_WAVE_EXT_MAX} pts)...")
    sigs_core = [s for s in sigs_all
                 if s['gap_pts_at_lon'] < CORE_GAP_MAX and s['wave_ext_pts'] < CORE_WAVE_EXT_MAX]
    print(f"  {len(sigs_core)} ATTR signals pass CORE filter  "
          f"({len(sigs_core)/len(sigs_all)*100:.0f}% of all ATTR signals)")

    # Distribution check
    gaps = [s['gap_pts_at_lon'] for s in sigs_core]
    exts = [s['wave_ext_pts']   for s in sigs_core]
    print(f"  Gap at London:   median={np.median(gaps):.0f}  mean={np.mean(gaps):.0f}  "
          f"max={max(gaps):.0f}")
    print(f"  Wave extension:  median={np.median(exts):.0f}  mean={np.mean(exts):.0f}  "
          f"max={max(exts):.0f}")

    # DXY outcome of filtered signals
    dxy_wins   = sum(1 for s in sigs_core if s['outcome'] == 'win')
    dxy_losses = sum(1 for s in sigs_core if s['outcome'] == 'loss')
    dxy_wr     = dxy_wins / (dxy_wins + dxy_losses) * 100 if (dxy_wins + dxy_losses) > 0 else 0
    print(f"\n  DXY outcome of CORE signals: {dxy_wins}W / {dxy_losses}L  WR={dxy_wr:.1f}%  "
          f"(DXY trades as baseline)")

    # ── Step 3: Apply to all pairs — unfiltered baseline ────────────────────
    print("\nApplying to pairs (DXY-exit)...")
    pair_trades_all  = []
    pair_trades_core = []

    for pair in PAIRS:
        pt_all  = apply_dxy_exit(sigs_all,  pair_dfs[pair], pair, news_dates)
        pt_core = apply_dxy_exit(sigs_core, pair_dfs[pair], pair, news_dates)
        pair_trades_all.extend(pt_all)
        pair_trades_core.extend(pt_core)

    # ── Results: unfiltered ──────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("  BASELINE: ALL ATTR SIGNALS (no CORE filter)  — DXY-exit")
    print("=" * 80)
    print(f"\n  {'Pair':<10}  {'N':>4}  {'W':>4} {'L':>4}  {'WR%':>6}  {'PF':>6}  "
          f"{'Net R':>8}  {'AvgW':>7}  {'AvgL':>7}")
    print(f"  {'-'*72}")
    for pair in PAIRS:
        pt = [t for t in pair_trades_all if t['pair'] == pair]
        s  = stats_r(pt)
        if s['N'] == 0:
            print(f"  {pair:<10}  -- no matching bars")
            continue
        pf = f"{s['PF']:.3f}" if s['PF'] != float('inf') else "   inf"
        print(f"  {pair:<10}  {s['N']:>4}  {s['W']:>4} {s['L']:>4}  "
              f"{s['WR']:>5.1f}%  {pf:>6}  {s['NetR']:>+8.2f}R  "
              f"{s['AvgW']:>+6.3f}R  {s['AvgL']:>-6.3f}R")
    print(f"  {'-'*72}")
    tot = stats_r(pair_trades_all)
    pf_all = "   inf" if tot['PF'] == float('inf') else f"{tot['PF']:.3f}"
    print(f"  {'PORTFOLIO':<10}  {tot['N']:>4}  {tot['W']:>4} {tot['L']:>4}  "
          f"{tot['WR']:>5.1f}%  {pf_all:>6}  {tot['NetR']:>+8.2f}R")

    # ── Results: CORE filtered ───────────────────────────────────────────────
    print()
    print("=" * 80)
    print(f"  CORE FILTER: gap < {CORE_GAP_MAX} pts  AND  wave_ext < {CORE_WAVE_EXT_MAX} pts  — DXY-exit")
    print("=" * 80)
    print(f"\n  {'Pair':<10}  {'N':>4}  {'W':>4} {'L':>4}  {'WR%':>6}  {'PF':>6}  "
          f"{'Net R':>8}  {'AvgW':>7}  {'AvgL':>7}")
    print(f"  {'-'*72}")
    for pair in PAIRS:
        pt = [t for t in pair_trades_core if t['pair'] == pair]
        s  = stats_r(pt)
        if s['N'] == 0:
            print(f"  {pair:<10}  -- no matching bars")
            continue
        pf = f"{s['PF']:.3f}" if s['PF'] != float('inf') else "   inf"
        print(f"  {pair:<10}  {s['N']:>4}  {s['W']:>4} {s['L']:>4}  "
              f"{s['WR']:>5.1f}%  {pf:>6}  {s['NetR']:>+8.2f}R  "
              f"{s['AvgW']:>+6.3f}R  {s['AvgL']:>-6.3f}R")
    print(f"  {'-'*72}")
    tot = stats_r(pair_trades_core)
    pf_str = "   inf" if tot['PF'] == float('inf') else f"{tot['PF']:.3f}"
    print(f"  {'PORTFOLIO':<10}  {tot['N']:>4}  {tot['W']:>4} {tot['L']:>4}  "
          f"{tot['WR']:>5.1f}%  {pf_str:>6}  {tot['NetR']:>+8.2f}R  "
          f"{tot['AvgW']:>+6.3f}R  {tot['AvgL']:>-6.3f}R")

    # ── Monthly P&L (portfolio) ──────────────────────────────────────────────
    print("\n  Monthly portfolio P&L (CORE filter, all pairs combined):")
    monthly_summary(pair_trades_core)

    # ── Signal-level direction breakdown ────────────────────────────────────
    print()
    print("=" * 80)
    print("  CORE: LONG vs SHORT signal breakdown (all pairs combined)")
    print("=" * 80)
    for sig_type in ['ATTR_LONG', 'ATTR_SHORT']:
        pt = [t for t in pair_trades_core if t['dxy_type'] == sig_type]
        s  = stats_r(pt)
        pf = f"{s['PF']:.3f}" if s['PF'] != float('inf') else "   inf"
        print(f"  {sig_type:<14}  N={s['N']:>3}  W={s['W']:>3} L={s['L']:>3}  "
              f"WR={s['WR']:>5.1f}%  PF={pf}  NetR={s['NetR']:>+7.2f}R")

    # ── Dollar estimate ──────────────────────────────────────────────────────
    months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.4
    ACCOUNT  = 100_000
    RISK_PCT = 0.0025   # 0.25% per trade
    risk_per_trade = ACCOUNT * RISK_PCT

    print()
    print("=" * 80)
    print(f"  DOLLAR ESTIMATE — CORE filter  (Account: ${ACCOUNT:,}  |  Risk: {RISK_PCT*100:.2f}% = ${risk_per_trade:,.0f}/trade)")
    print("=" * 80)
    print(f"\n  {'Pair':<10}  {'Trades':>7}  {'WR%':>6}  {'Net R':>8}  {'Est. Profit':>12}")
    print(f"  {'-'*52}")
    total_r, total_usd = 0.0, 0.0
    for pair in PAIRS:
        pt = [t for t in pair_trades_core if t['pair'] == pair]
        s  = stats_r(pt)
        if s['N'] == 0:
            continue
        dollar = s['NetR'] * risk_per_trade
        total_r   += s['NetR']
        total_usd += dollar
        sign = '+' if dollar >= 0 else ''
        print(f"  {pair:<10}  {s['N']:>7}  {s['WR']:>5.1f}%  {s['NetR']:>+8.2f}R  "
              f"{sign}${dollar:>10,.0f}")
    print(f"  {'-'*52}")
    sign = '+' if total_usd >= 0 else ''
    ann  = total_usd / months * 12
    ann_r = total_r / months * 12
    print(f"  {'TOTAL':<10}  {'':>7}  {'':>6}  {total_r:>+8.2f}R  {sign}${total_usd:>10,.0f}")
    print(f"\n  Period:            {months:.0f} months")
    print(f"  Net R (period):    {total_r:>+.2f}R")
    print(f"  Annualised R:      {ann_r:>+.1f}R/year")
    print(f"  Estimated profit:  {sign}${total_usd:,.0f}  over {months:.0f} months")
    print(f"  Annualised:        {'+' if ann>=0 else ''}${ann:,.0f}/year")
    print(f"  Return on account: {'+' if total_usd>=0 else ''}{total_usd/ACCOUNT*100:.1f}%  "
          f"/ {'+' if ann>=0 else ''}{ann/ACCOUNT*100:.1f}% annualised")

    # ── Save outputs ──────────────────────────────────────────────────────────
    pd.DataFrame(sigs_core).to_csv(BASE / 'attr_core_signals.csv', index=False)
    pd.DataFrame(pair_trades_core).to_csv(BASE / 'attr_core_pair_trades.csv', index=False)
    print(f"\n  Saved: attr_core_signals.csv  ({len(sigs_core)} signals)")
    print(f"  Saved: attr_core_pair_trades.csv  ({len(pair_trades_core)} pair trades)")


if __name__ == '__main__':
    main()
