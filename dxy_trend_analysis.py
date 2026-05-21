"""
DXY Trend Condition Analysis
Runs the backtest, then for each trade entry computes multiple trend-strength
metrics and sweeps thresholds to find which market conditions produce the best
win rate and profit factor.

Metrics tested:
  1. ADX(14)           -- standard trend-strength indicator (direction-neutral)
  2. 4H EMA distance   -- |close - EMA(n)| / ATR(14), direction-aware version in indicator
  3. ATR ratio         -- current ATR(14) / rolling_mean(ATR, 50): high = expanding volatility
  4. Donchian width    -- (highest_high - lowest_low, 20) / ATR(14): measures range width
  5. Day range/ATR     -- (day_high - day_low) / ATR(14): intraday expansion vs average

Usage: python dxy_trend_analysis.py
"""

import pandas as pd
import numpy as np
import sys, os

# ── Import the backtester ───────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
import dxy_backtest as bt

# ── Parameters ──────────────────────────────────────────────────────────────
bt.ATTR_MIN_PTS  = 500
bt.ATTR_MAX_PTS  = 2000
bt.REV_MIN_SL    = 3000
bt.REV_MAX_DIST  = 500
bt.DIV_LOOKBACK  = 15
bt.REV_MIN_DIV   = 1
bt.EXIT_MODE     = 'intrabar'

EMA_LEN  = 50     # 4H EMA length (same as indicator default)
ADX_LEN  = 14
ATR_LEN  = 14
DONCH_LEN = 20
ATR_NORM_WINDOW = 50  # bars for ATR ratio baseline

# ── Indicator helpers ────────────────────────────────────────────────────────

def atr(df, period=14):
    prev_close = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low']  - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()

def adx(df, period=14):
    """
    Wilder's ADX.
    TR/+DM/-DM use Wilder's adjusted-sum smoothing (seed = sum of first n).
    DX->ADX uses Wilder's adjusted-average smoothing (seed = mean of first n).
    This keeps ADX in the correct 0-100 range.
    """
    prev_high  = df['high'].shift(1)
    prev_low   = df['low'].shift(1)
    prev_close = df['close'].shift(1)

    plus_dm  = np.where((df['high'] - prev_high) > (prev_low - df['low']),
                        np.maximum(df['high'] - prev_high, 0), 0)
    minus_dm = np.where((prev_low - df['low']) > (df['high'] - prev_high),
                        np.maximum(prev_low - df['low'], 0), 0)

    tr_val = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low']  - prev_close).abs()
    ], axis=1).max(axis=1)

    # Wilder adjusted-SUM smoothing (for TR, DM — keeps running totals)
    def wilder_sum(s, n):
        result = np.zeros(len(s))
        s_arr  = np.nan_to_num(s.values if hasattr(s, 'values') else np.array(s))
        result[n-1] = s_arr[:n].sum()
        for i in range(n, len(s)):
            result[i] = result[i-1] - result[i-1] / n + s_arr[i]
        return pd.Series(result, index=s.index if hasattr(s, 'index') else None)

    # Wilder adjusted-MEAN smoothing (for ADX — keeps values in 0-100 range)
    def wilder_mean(s, n):
        result = np.full(len(s), np.nan)
        s_arr  = np.nan_to_num(s.values if hasattr(s, 'values') else np.array(s))
        # Seed: simple mean of first n valid DX values (starting from 2*n to allow DX to form)
        seed_start = 2 * n - 1
        if seed_start >= len(s):
            return pd.Series(result, index=s.index if hasattr(s, 'index') else None)
        result[seed_start] = s_arr[n:seed_start+1].mean()
        for i in range(seed_start + 1, len(s)):
            result[i] = (result[i-1] * (n-1) + s_arr[i]) / n
        return pd.Series(result, index=s.index if hasattr(s, 'index') else None)

    tr_s   = pd.Series(tr_val.values,  index=df.index)
    pdm_s  = pd.Series(plus_dm,        index=df.index)
    mdm_s  = pd.Series(minus_dm,       index=df.index)

    tr_sm  = wilder_sum(tr_s,  period)
    pdm_sm = wilder_sum(pdm_s, period)
    mdm_sm = wilder_sum(mdm_s, period)

    di_plus  = np.where(tr_sm > 0, pdm_sm / tr_sm * 100, 0)
    di_minus = np.where(tr_sm > 0, mdm_sm / tr_sm * 100, 0)
    dx       = np.where((di_plus + di_minus) > 0,
                        np.abs(di_plus - di_minus) / (di_plus + di_minus) * 100, 0)
    dx_s     = pd.Series(dx, index=df.index)
    adx_val  = wilder_mean(dx_s, period)
    return adx_val

def resample_4h(df):
    """Resample 15m OHLC to 4H bars."""
    df_t = df.set_index(pd.to_datetime(df['time'], utc=True))
    r = df_t[['open','high','low','close']].resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    return r

# ── Build trade dataset with trend metrics ────────────────────────────────────

def build_trade_metrics():
    print("Loading data and running backtest...")
    df_raw = pd.read_csv(bt.CSV_PATH, low_memory=False)
    df = df_raw[['time','open','high','low','close']].copy()
    df = df.sort_values('time').reset_index(drop=True)
    df[['open','high','low','close']] = df[['open','high','low','close']].astype(float)

    # ── Run backtest to get trades ──────────────────────────────────────────
    trades = bt.run_backtest()
    if not trades:
        print("No trades found.")
        return None, None

    tdf = pd.DataFrame(trades)
    tdf = tdf[tdf['outcome'].isin(['win','loss'])]
    tdf['win'] = (tdf['outcome'] == 'win').astype(int)

    # ── Compute 15m-based trend metrics ────────────────────────────────────
    print("Computing trend metrics...")
    df['atr14']    = atr(df, ATR_LEN)
    df['adx14']    = adx(df, ADX_LEN)
    df['ema50']    = df['close'].ewm(span=EMA_LEN, adjust=False).mean()
    df['ema_dist'] = (df['close'] - df['ema50']).abs() / df['atr14']
    df['atr_ratio']= df['atr14'] / df['atr14'].rolling(ATR_NORM_WINDOW).mean()
    df['donch_w']  = (df['high'].rolling(DONCH_LEN).max() -
                      df['low'].rolling(DONCH_LEN).min()) / df['atr14']

    # Day range: distance from UTC midnight open to bar
    df['ts']       = pd.to_datetime(df['time'], utc=True)
    df['date']     = df['ts'].dt.date
    day_hi         = df.groupby('date')['high'].transform('max')
    day_lo         = df.groupby('date')['low'].transform('min')
    df['day_range_atr'] = (day_hi - day_lo) / df['atr14']

    # ── Compute 4H ADX separately, forward-fill onto 15m bars ─────────────
    df4 = resample_4h(df)
    df4['atr14_4h']    = atr(df4, ADX_LEN)
    df4['adx14_4h']    = adx(df4, ADX_LEN)
    df4['ema50_4h']    = df4['close'].ewm(span=EMA_LEN, adjust=False).mean()
    df4['ema_dist_4h'] = (df4['close'] - df4['ema50_4h']).abs() / df4['atr14_4h']

    # Build a dict: truncated-to-4H epoch → metric values
    # For each 15m bar, floor its UTC hour to the nearest 4H boundary
    def floor4h(ts):
        """Given a pandas Timestamp, return UTC floor to nearest 4-hour boundary as int."""
        epoch_sec = ts.timestamp()
        return int(epoch_sec // (4 * 3600)) * (4 * 3600)

    adx4h_map  = {}
    edist4h_map = {}
    for ts_idx, row4 in df4.iterrows():
        key = floor4h(ts_idx)
        adx4h_map[key]   = row4['adx14_4h']
        edist4h_map[key] = row4['ema_dist_4h']

    # Assign 4H values to each 15m bar by flooring timestamp
    ts_15m = pd.to_datetime(df['time'], utc=True)
    df['adx14_4h']    = [adx4h_map.get(floor4h(t), np.nan) for t in ts_15m]
    df['ema_dist_4h'] = [edist4h_map.get(floor4h(t), np.nan) for t in ts_15m]

    # ── Attach metrics to each trade ───────────────────────────────────────
    df_indexed = df.set_index('time')

    metrics_cols = ['adx14','adx14_4h','ema_dist','ema_dist_4h',
                    'atr_ratio','donch_w','day_range_atr']

    for col in metrics_cols:
        tdf[col] = tdf['entry_time'].map(
            lambda t: df_indexed[col].get(t, np.nan)
        )

    return tdf, df

# ── Threshold sweep ───────────────────────────────────────────────────────────

def sweep_threshold(tdf, metric_col, label, steps=20, min_trades=4):
    """
    For each threshold value, compute WR and PF of trades ABOVE that threshold.
    Returns a DataFrame of results.
    """
    vals = tdf[metric_col].dropna()
    if len(vals) == 0:
        return None

    lo, hi = vals.quantile(0.05), vals.quantile(0.95)
    thresholds = np.linspace(lo, hi, steps)

    rows = []
    for thr in thresholds:
        subset = tdf[tdf[metric_col] >= thr]
        n = len(subset)
        if n < min_trades:
            continue
        wins   = subset['win'].sum()
        losses = n - wins
        wr     = wins / n * 100
        gross_w = subset[subset['win']==1]['sl_pts'].sum()
        gross_l = subset[subset['win']==0]['sl_pts'].sum()
        pf = gross_w / gross_l if gross_l > 0 else np.inf
        rows.append({'threshold': round(thr, 2), 'trades': n, 'wins': wins,
                     'losses': losses, 'wr_pct': round(wr, 1), 'pf': round(pf, 3)})

    return pd.DataFrame(rows) if rows else None

# ── Reporting ─────────────────────────────────────────────────────────────────

def find_optimal(sweep_df):
    """Return the row that maximises PF with at least 5 trades."""
    if sweep_df is None or len(sweep_df) == 0:
        return None
    valid = sweep_df[sweep_df['trades'] >= 5]
    if len(valid) == 0:
        return None
    return valid.loc[valid['pf'].idxmax()]

def report_metric(tdf, metric_col, label, steps=25):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    # Full sample stats
    full = tdf[tdf[metric_col].notna()]
    print(f"  Metric range in trades: "
          f"{full[metric_col].min():.2f} – {full[metric_col].max():.2f}  "
          f"(median {full[metric_col].median():.2f})")

    # Correlation with win
    corr = full[metric_col].corr(full['win'])
    print(f"  Correlation with win:   {corr:+.3f}")

    # Sweep
    sw = sweep_threshold(tdf, metric_col, label, steps=steps)
    if sw is None or len(sw) == 0:
        print("  Not enough data to sweep.")
        return

    print(f"\n  Threshold sweep (trades WITH metric >= threshold):")
    print(f"  {'Threshold':>10}  {'Trades':>7}  {'WR%':>6}  {'PF':>6}")
    print(f"  {'-'*38}")
    for _, row in sw.iterrows():
        marker = ' <-- BEST PF' if row['pf'] == sw[sw['trades']>=5]['pf'].max() else ''
        print(f"  {row['threshold']:>10.2f}  {int(row['trades']):>7}  "
              f"{row['wr_pct']:>6.1f}  {row['pf']:>6.3f}{marker}")

    opt = find_optimal(sw)
    if opt is not None:
        print(f"\n  Optimal threshold: {metric_col} >= {opt['threshold']:.2f}")
        print(f"    Trades: {int(opt['trades'])}  |  WR: {opt['wr_pct']}%  |  PF: {opt['pf']}")

def report_combined(tdf):
    """Show trade outcomes split by ADX quartile bands."""
    print(f"\n{'='*60}")
    print("  TRADE OUTCOMES BY 4H ADX BAND")
    print(f"{'='*60}")
    bins   = [0, 15, 20, 25, 35, 100]
    labels = ['<15 (flat)', '15-20 (weak)', '20-25 (developing)',
              '25-35 (trending)', '>35 (strong trend)']
    tdf2 = tdf[tdf['adx14_4h'].notna()].copy()
    tdf2['adx_band'] = pd.cut(tdf2['adx14_4h'], bins=bins, labels=labels)
    grp = tdf2.groupby('adx_band', observed=True).agg(
        trades=('win', 'count'),
        wins=('win', 'sum')
    )
    grp['losses'] = grp['trades'] - grp['wins']
    grp['wr_pct'] = (grp['wins'] / grp['trades'] * 100).round(1)
    grp['gross_w'] = tdf2[tdf2['win']==1].groupby(
        pd.cut(tdf2[tdf2['win']==1]['adx14_4h'], bins=bins, labels=labels),
        observed=True)['sl_pts'].sum()
    grp['gross_l'] = tdf2[tdf2['win']==0].groupby(
        pd.cut(tdf2[tdf2['win']==0]['adx14_4h'], bins=bins, labels=labels),
        observed=True)['sl_pts'].sum()
    grp['pf'] = (grp['gross_w'] / grp['gross_l'].replace(0, np.nan)).round(3)

    print(f"\n  {'Band':<22}  {'Trades':>7}  {'WR%':>6}  {'PF':>6}")
    print(f"  {'-'*50}")
    for band, row in grp.iterrows():
        if row['trades'] == 0:
            continue
        pf_str = f"{row['pf']:.3f}" if not np.isnan(row['pf']) else "  inf"
        print(f"  {str(band):<22}  {int(row['trades']):>7}  "
              f"{row['wr_pct']:>6.1f}  {pf_str:>6}")

    print(f"\n  {'Band':<22}  REV trades / WR     ATTR trades / WR")
    print(f"  {'-'*60}")
    for band_label in labels:
        band_trades = tdf2[tdf2['adx_band'] == band_label]
        rev   = band_trades[band_trades['type'].str.startswith('REV')]
        attr  = band_trades[band_trades['type'].str.startswith('ATTR')]
        rev_wr  = f"{rev['win'].mean()*100:.0f}%" if len(rev) > 0  else "  -"
        attr_wr = f"{attr['win'].mean()*100:.0f}%" if len(attr) > 0 else "  -"
        print(f"  {band_label:<22}  {len(rev):>3} trades / {rev_wr:<8}   "
              f"{len(attr):>3} trades / {attr_wr}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    tdf, df = build_trade_metrics()
    if tdf is None:
        return

    print(f"\n{'='*60}")
    print(f"  TRADE SAMPLE: {len(tdf)} trades  "
          f"(Wins: {tdf['win'].sum()}  Losses: {(1-tdf['win']).sum()})")
    print(f"{'='*60}")

    # Per-metric threshold sweeps
    metrics = [
        ('adx14_4h',      '4H ADX(14)  [trend strength on 4H bars]'),
        ('adx14',         '15m ADX(14) [intraday trend strength]'),
        ('ema_dist_4h',   '4H EMA distance  [|close-EMA50| / ATR14]'),
        ('ema_dist',      '15m EMA distance [|close-EMA50| / ATR14]'),
        ('atr_ratio',     'ATR Ratio [current ATR / 50-bar avg ATR]'),
        ('donch_w',       'Donchian Width [range(20) / ATR14]'),
        ('day_range_atr', 'Day Range / ATR14'),
    ]

    for col, lbl in metrics:
        report_metric(tdf, col, lbl)

    # ADX band breakdown
    report_combined(tdf)

    # Save enriched trade log
    out = bt.CSV_PATH.replace('.csv', '_TREND_ANALYSIS.csv')
    tdf.to_csv(out, index=False)
    print(f"\nEnriched trade log saved: {out}")

    # Final recommendation
    print(f"\n{'='*60}")
    print("  SUMMARY & PINE SCRIPT RECOMMENDATION")
    print(f"{'='*60}")

    # Find best ADX threshold
    sw_adx = sweep_threshold(tdf, 'adx14_4h', '4H ADX', steps=30)
    opt = find_optimal(sw_adx)
    if opt is not None:
        print(f"\n  Best single filter: 4H ADX >= {opt['threshold']:.1f}")
        print(f"  Result: {int(opt['trades'])} trades | "
              f"WR {opt['wr_pct']}% | PF {opt['pf']}")

    # Reversal only + ADX
    rev_only = tdf[tdf['type'].str.startswith('REV')]
    sw_rev = sweep_threshold(rev_only, 'adx14_4h', '4H ADX (reversals only)', steps=25)
    opt_rev = find_optimal(sw_rev)
    if opt_rev is not None:
        print(f"\n  Reversals only + 4H ADX >= {opt_rev['threshold']:.1f}")
        print(f"  Result: {int(opt_rev['trades'])} trades | "
              f"WR {opt_rev['wr_pct']}% | PF {opt_rev['pf']}")

    print(f"\n  Pine Script inputs to add:")
    adx_thr = opt['threshold'] if opt is not None else 20.0
    print(f"    use_adx_gate  = input.bool(true,  'Enable ADX Trend Gate')")
    print(f"    adx_len       = input.int(14,     'ADX Length', minval=5, maxval=50)")
    print(f"    adx_tf        = input.timeframe('240', 'ADX Timeframe')")
    print(f"    adx_min       = input.float({adx_thr:.1f},  'Min ADX (0=off)', "
          f"minval=0, maxval=60, step=0.5)")
    print(f"\n  Pine Script logic:")
    print(f"    _4h_adx   = request.security(syminfo.tickerid, adx_tf,")
    print(f"                    ta.dmi(adx_len, adx_len)[1], lookahead=barmerge.lookahead_off)")
    print(f"    adx_ok    = not use_adx_gate or _4h_adx >= adx_min")
    print(f"    // Apply to BOTH attraction and reversal entry conditions")

if __name__ == '__main__':
    main()
