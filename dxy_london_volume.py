"""
dxy_london_volume.py
====================
Adds CME futures London-session volume to the pattern match analysis.

Volume sources (yfinance, 1h bars):
  6E=F  -> EURUSD proxy  (EUR/USD CME futures)
  6J=F  -> USDJPY proxy  (JPY/USD CME futures)
  6C=F  -> USDCAD proxy  (CAD/USD CME futures)
  GC=F  -> XAUUSD proxy  (Gold CME futures)
  DXY   -> NO volume available (OTC index, no exchange-traded futures on yfinance)

Method:
  1. Download 1h futures data for each pair proxy
  2. Compute daily London session volume (07:00-15:59 UTC)
  3. Compute 20-day rolling average London volume -> relative volume ratio
  4. Classify days as LOW / NORMAL / HIGH / SPIKE volume
  5. Join to existing pattern match table (dxy_london_patterns_v2.csv)
  6. Test: does relative volume predict pattern matches, impulse moves, reversals?

Hypothesis: large impulsive London moves are driven by above-average volume
            -> volume spike days should show higher match rates, especially for REVERSALS
"""

import pandas as pd
import numpy as np
import yfinance as yf
from pathlib import Path

# ─── PATHS ────────────────────────────────────────────────────────────────────
BASE = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
PATTERN_CSV = BASE / "dxy_london_patterns_v2.csv"

# ─── CONFIG ───────────────────────────────────────────────────────────────────
# CME futures tickers mapped to the pairs we trade
FUTURES_MAP = {
    'EURUSD': '6E=F',
    'USDJPY': '6J=F',
    'USDCAD': '6C=F',
    'XAUUSD': 'GC=F',
}
PAIRS = list(FUTURES_MAP.keys())

# London session (UTC)
LON_START_H = 7
LON_END_H   = 16   # exclusive

# Rolling window for "normal" volume baseline
VOL_WINDOW = 20

# Volume classification thresholds (relative to rolling average)
VOL_LOW    = 0.60   # < 60% of average = quiet day
VOL_HIGH   = 1.40   # > 140% = elevated
VOL_SPIKE  = 2.00   # > 200% = spike

# ─── DOWNLOAD FUTURES VOLUME ──────────────────────────────────────────────────
def download_futures_volume(ticker, pair):
    print(f"  Downloading {ticker} ({pair} proxy)...")
    try:
        df = yf.download(ticker, period="2y", interval="1h",
                         progress=False, auto_adjust=True)
        if df.empty:
            print(f"    WARNING: no data for {ticker}")
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[['Close', 'Volume']].copy()
        df.columns = ['close', 'volume']
        df['volume'] = df['volume'].fillna(0)
        return df
    except Exception as e:
        print(f"    ERROR downloading {ticker}: {e}")
        return pd.DataFrame()

# ─── COMPUTE DAILY LONDON VOLUME ─────────────────────────────────────────────
def daily_london_volume(df_1h):
    """Sum volume over London hours each day. Returns Series indexed by date."""
    if df_1h.empty:
        return pd.Series(dtype=float)
    lon = df_1h[(df_1h.index.hour >= LON_START_H) & (df_1h.index.hour < LON_END_H)]
    daily = lon.groupby(lon.index.date)['volume'].sum()
    # Remove zero-volume days (futures closed / holiday)
    daily = daily[daily > 0]
    return daily

def add_relative_volume(daily_vol):
    """Add rolling 20-day average and relative volume ratio."""
    df = daily_vol.to_frame(name='london_vol')
    df['vol_avg20']  = df['london_vol'].rolling(VOL_WINDOW, min_periods=5).mean()
    df['vol_ratio']  = df['london_vol'] / df['vol_avg20'].replace(0, np.nan)
    df['vol_class']  = pd.cut(
        df['vol_ratio'],
        bins=[0, VOL_LOW, VOL_HIGH, VOL_SPIKE, 999],
        labels=['LOW', 'NORMAL', 'HIGH', 'SPIKE'],
        right=False
    )
    return df

# ─── VOLUME ANALYSIS AGAINST PATTERN MATCHES ─────────────────────────────────
def vol_match_table(pat_df, pair, vol_df):
    """
    For each volume class, show:
      - number of active-pattern days
      - pattern match rate
      - % of attraction vs reversal days
      - mean DXY net move magnitude
    """
    active = pat_df[pat_df['DXY_pattern'].isin(['ATTRACTION','REVERSAL'])].copy()
    merged = active.merge(vol_df[['vol_ratio','vol_class']],
                          left_on='date', right_index=True, how='left')
    rows = []
    for cls in ['LOW', 'NORMAL', 'HIGH', 'SPIKE']:
        sub = merged[merged['vol_class'] == cls]
        if len(sub) == 0:
            continue
        mr   = sub[f'{pair}_match'].mean() * 100
        attr = (sub['DXY_pattern'] == 'ATTRACTION').mean() * 100
        rev  = (sub['DXY_pattern'] == 'REVERSAL').mean() * 100
        mean_net = sub['DXY_net_pts'].abs().mean()
        rows.append({
            'Vol class': cls,
            'N days'   : len(sub),
            'Match%'   : round(mr, 1),
            'Attract%' : round(attr, 1),
            'Reversal%': round(rev, 1),
            'Avg |DXY net|': round(mean_net, 0),
            'Avg ratio': round(sub['vol_ratio'].mean(), 2),
        })
    return pd.DataFrame(rows)

def vol_threshold_scan(pat_df, pair, vol_df, steps=20):
    """
    Scan relative volume thresholds: what ratio cutoff maximises match rate lift?
    """
    active = pat_df[pat_df['DXY_pattern'].isin(['ATTRACTION','REVERSAL'])].copy()
    merged = active.merge(vol_df[['vol_ratio']], left_on='date', right_index=True, how='left')
    merged = merged.dropna(subset=['vol_ratio'])
    base   = merged[f'{pair}_match'].mean() * 100
    thresholds = np.linspace(0.5, 3.0, steps)
    rows = []
    for thr in thresholds:
        above = merged[merged['vol_ratio'] >= thr]
        below = merged[merged['vol_ratio'] <  thr]
        if len(above) < 4: continue
        mr_above = above[f'{pair}_match'].mean() * 100
        mr_below = below[f'{pair}_match'].mean() * 100 if len(below) >= 4 else np.nan
        rows.append({
            'threshold': round(thr, 2),
            'N_above'  : len(above),
            'match_above': round(mr_above, 1),
            'lift_above' : round(mr_above - base, 1),
            'N_below'  : len(below),
            'match_below': round(mr_below, 1) if not np.isnan(mr_below) else np.nan,
        })
    return pd.DataFrame(rows), base

def vol_impulse_correlation(pat_df, pair, vol_df):
    """
    Does higher volume correlate with larger net moves on the pair?
    Split by low/high volume and compare pair net move magnitude.
    """
    active = pat_df[pat_df['DXY_pattern'].isin(['ATTRACTION','REVERSAL'])].copy()
    merged = active.merge(vol_df[['vol_ratio']], left_on='date', right_index=True, how='left')
    merged = merged.dropna(subset=['vol_ratio', f'{pair}_net_pts'])
    corr   = merged['vol_ratio'].corr(merged[f'{pair}_net_pts'].abs())
    return corr, merged

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    # Load pattern data
    print("Loading pattern match data...")
    pat_df = pd.read_csv(PATTERN_CSV)
    pat_df['date'] = pd.to_datetime(pat_df['date']).dt.date
    act    = pat_df[pat_df['DXY_pattern'].isin(['ATTRACTION','REVERSAL'])]

    # Download and compute volume
    print("\nDownloading futures volume (1h bars)...")
    vol_data = {}
    for pair, ticker in FUTURES_MAP.items():
        raw    = download_futures_volume(ticker, pair)
        if raw.empty:
            continue
        daily  = daily_london_volume(raw)
        vol_df = add_relative_volume(daily)
        vol_data[pair] = vol_df
        print(f"    {pair}: {len(vol_df)} trading days, avg London vol = {vol_df['london_vol'].mean():.0f}")

    print()

    # ─── SECTION 1: VOLUME OVERVIEW ───────────────────────────────────────────
    print("=" * 72)
    print("  1. LONDON SESSION VOLUME OVERVIEW  (CME futures proxy, 1h bars)")
    print("=" * 72)
    print()
    for pair, ticker in FUTURES_MAP.items():
        if pair not in vol_data:
            print(f"  {pair} ({ticker}): NO DATA")
            continue
        vd = vol_data[pair]
        overlapping = vd[pd.Index(vd.index).isin(pat_df['date'])]
        print(f"  {pair} proxy = {ticker}")
        print(f"    Dataset overlap: {len(overlapping)} of {len(act)} active-pattern days have volume data")
        print(f"    Median daily London vol : {vd['london_vol'].median():.0f}")
        print(f"    Vol class distribution  : ", end='')
        vc = vd['vol_class'].value_counts().sort_index()
        print('  '.join([f"{k}={v}" for k, v in vc.items()]))
        print()

    # ─── SECTION 2: VOLUME CLASS vs PATTERN MATCH ─────────────────────────────
    print("=" * 72)
    print("  2. VOLUME CLASS vs PATTERN MATCH RATE")
    print("     Does higher volume predict better pattern alignment?")
    print("=" * 72)
    for pair in PAIRS:
        if pair not in vol_data:
            continue
        base = act[f'{pair}_match'].mean() * 100
        tbl  = vol_match_table(pat_df, pair, vol_data[pair])
        print(f"\n  {pair}  (baseline match rate: {base:.1f}%)")
        print(f"  {'Vol Class':<10} {'N':>5} {'Match%':>7} {'Lift':>7} {'Attract%':>9} {'Reversal%':>10} {'Avg|DXYnet|':>12} {'AvgRatio':>9}")
        print(f"  {'-'*72}")
        for _, r in tbl.iterrows():
            lift = r['Match%'] - base
            sign = '+' if lift >= 0 else ''
            print(f"  {r['Vol class']:<10} {r['N days']:>5} {r['Match%']:>6.1f}% {sign}{lift:>+5.1f}pp "
                  f"{r['Attract%']:>8.1f}% {r['Reversal%']:>9.1f}% {r['Avg |DXY net|']:>11.0f} {r['Avg ratio']:>9.2f}")

    print()

    # ─── SECTION 3: THRESHOLD SCAN ────────────────────────────────────────────
    print("=" * 72)
    print("  3. RELATIVE VOLUME THRESHOLD SCAN")
    print("     What ratio cutoff most improves match rate?")
    print("=" * 72)
    for pair in PAIRS:
        if pair not in vol_data:
            continue
        tdf, base = vol_threshold_scan(pat_df, pair, vol_data[pair])
        if tdf.empty:
            continue
        # Find best lift
        best = tdf.loc[tdf['lift_above'].idxmax()]
        worst = tdf.loc[tdf['lift_above'].idxmin()]
        print(f"\n  {pair}  baseline={base:.1f}%")
        print(f"  Best  threshold: ratio>={best['threshold']:.2f}  N={int(best['N_above'])}  "
              f"match={best['match_above']:.1f}%  lift={best['lift_above']:+.1f}pp")
        print(f"  Worst threshold: ratio>={worst['threshold']:.2f}  N={int(worst['N_above'])}  "
              f"match={worst['match_above']:.1f}%  lift={worst['lift_above']:+.1f}pp")
        print()
        print(f"  {'Ratio>=':<9} {'N_above':>8} {'Match%':>8} {'Lift':>7}  | {'N_below':>8} {'Match%':>8}")
        print(f"  {'-'*60}")
        # Print key breakpoints
        key_rows = tdf[tdf['threshold'].isin([0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00, 2.50])].copy()
        for _, r in key_rows.iterrows():
            sign = '+' if r['lift_above'] >= 0 else ''
            below_str = f"{r['match_below']:.1f}%" if not np.isnan(r['match_below']) else "  n/a"
            print(f"  {r['threshold']:<9.2f} {int(r['N_above']):>8} {r['match_above']:>7.1f}% {sign}{r['lift_above']:>+5.1f}pp"
                  f"  | {int(r['N_below']):>8} {below_str:>8}")

    print()

    # ─── SECTION 4: VOLUME vs IMPULSE MAGNITUDE ───────────────────────────────
    print("=" * 72)
    print("  4. VOLUME vs IMPULSE MAGNITUDE")
    print("     Correlation between relative volume and net pair move (|pts|)")
    print("=" * 72)
    print()
    for pair in PAIRS:
        if pair not in vol_data:
            continue
        corr, merged = vol_impulse_correlation(pat_df, pair, vol_data[pair])
        # Split into low (<avg) and high (>avg) volume days
        low_v  = merged[merged['vol_ratio'] <  1.0]
        high_v = merged[merged['vol_ratio'] >= 1.0]
        spike_v = merged[merged['vol_ratio'] >= 1.5]
        net_low   = low_v[f'{pair}_net_pts'].abs().mean()
        net_high  = high_v[f'{pair}_net_pts'].abs().mean()
        net_spike = spike_v[f'{pair}_net_pts'].abs().mean() if len(spike_v) else np.nan
        print(f"  {pair}:  vol-vs-impulse correlation = {corr:+.3f}")
        print(f"    Low vol days  (ratio <1.0):  N={len(low_v):3d}  avg |net| = {net_low:>8.0f} pts")
        print(f"    High vol days (ratio>=1.0):  N={len(high_v):3d}  avg |net| = {net_high:>8.0f} pts")
        if not np.isnan(net_spike):
            print(f"    Spike days    (ratio>=1.5):  N={len(spike_v):3d}  avg |net| = {net_spike:>8.0f} pts")
        print()

    # ─── SECTION 5: VOLUME + WPR COMBINED FILTER ──────────────────────────────
    print("=" * 72)
    print("  5. VOLUME + WPR COMBINED FILTER")
    print("     Does high volume amplify the WPR indicator signal?")
    print("=" * 72)

    # WPR filters that worked per pair (from v2 analysis)
    wpr_filters = {
        'USDCAD': ('DXY_wpr_os', 'DXY WPR oversold'),
        'USDJPY': ('USDJPY_wpr_ob', 'USDJPY WPR overbought'),
        'EURUSD': ('DXY_wpr_os', 'DXY WPR oversold'),
        'XAUUSD': ('XAUUSD_ema_bear', 'XAUUSD EMA bear'),
    }

    for pair in PAIRS:
        if pair not in vol_data:
            continue
        vd   = vol_data[pair]
        wpr_col, wpr_label = wpr_filters[pair]
        base = act[f'{pair}_match'].mean() * 100

        merged = act.merge(vd[['vol_ratio']], left_on='date', right_index=True, how='left')
        merged = merged.dropna(subset=['vol_ratio'])

        if wpr_col not in merged.columns:
            continue

        # Four quadrants: WPR on/off x volume above/below threshold
        vol_thr = 1.4
        wpr_on  = merged[merged[wpr_col] == True]
        wpr_off = merged[merged[wpr_col] == False]
        hi_vol  = merged[merged['vol_ratio'] >= vol_thr]
        lo_vol  = merged[merged['vol_ratio'] <  vol_thr]

        wpr_hi  = merged[(merged[wpr_col] == True) & (merged['vol_ratio'] >= vol_thr)]
        wpr_lo  = merged[(merged[wpr_col] == True) & (merged['vol_ratio'] <  vol_thr)]
        nwpr_hi = merged[(merged[wpr_col] == False) & (merged['vol_ratio'] >= vol_thr)]

        def mr(df): return df[f'{pair}_match'].mean()*100 if len(df) >= 3 else np.nan
        def fmt(v): return f"{v:.1f}%" if not np.isnan(v) else " n/a"

        print(f"\n  {pair}  (baseline {base:.1f}%,  WPR filter: {wpr_label},  vol threshold: {vol_thr}x)")
        print(f"  {'Condition':<45} {'N':>5}  {'Match%':>7}  {'Lift':>7}")
        print(f"  {'-'*65}")
        for label, df in [
            (f"{wpr_label} only", wpr_on),
            (f"High volume (ratio>={vol_thr}x) only", hi_vol),
            (f"{wpr_label} AND high volume", wpr_hi),
            (f"{wpr_label} AND low volume", wpr_lo),
            (f"No WPR AND high volume", nwpr_hi),
        ]:
            v = mr(df)
            lift = v - base if not np.isnan(v) else np.nan
            sign = '+' if (not np.isnan(lift) and lift >= 0) else ''
            print(f"  {label:<45} {len(df):>5}  {fmt(v):>7}  {sign}{lift:>+5.1f}pp" if not np.isnan(lift) else
                  f"  {label:<45} {len(df):>5}  {fmt(v):>7}   n/a")

    print()

    # ─── SECTION 6: REVERSAL DAYS + VOLUME ────────────────────────────────────
    print("=" * 72)
    print("  6. REVERSAL DAYS: are they high volume events?")
    print("=" * 72)
    print()
    rev_days = pat_df[pat_df['DXY_pattern'] == 'REVERSAL']
    attr_days = pat_df[pat_df['DXY_pattern'] == 'ATTRACTION']

    for pair in PAIRS:
        if pair not in vol_data:
            continue
        vd = vol_data[pair]
        rev_vol  = rev_days.merge(vd[['vol_ratio','vol_class']], left_on='date', right_index=True, how='left')
        attr_vol = attr_days.merge(vd[['vol_ratio','vol_class']], left_on='date', right_index=True, how='left')
        rev_vol  = rev_vol.dropna(subset=['vol_ratio'])
        attr_vol = attr_vol.dropna(subset=['vol_ratio'])

        rev_mean  = rev_vol['vol_ratio'].mean()
        attr_mean = attr_vol['vol_ratio'].mean()

        print(f"  {pair}:")
        print(f"    REVERSAL  days (N={len(rev_vol):2d}): avg vol ratio = {rev_mean:.2f}x  "
              f"  classes: {rev_vol['vol_class'].value_counts().to_dict()}")
        print(f"    ATTRACTION days (N={len(attr_vol):2d}): avg vol ratio = {attr_mean:.2f}x")
        ratio_lift = (rev_mean - attr_mean) / attr_mean * 100
        print(f"    Reversal days are {ratio_lift:+.0f}% higher volume than attraction days")
        print()

    # ─── SAVE ENRICHED DATASET ────────────────────────────────────────────────
    out = pat_df.copy()
    for pair in PAIRS:
        if pair not in vol_data:
            continue
        vd = vol_data[pair]
        joined = out['date'].map(vd['vol_ratio'].to_dict())
        out[f'{pair}_vol_ratio'] = joined
        joined_cls = out['date'].map(vd['vol_class'].astype(str).to_dict())
        out[f'{pair}_vol_class'] = joined_cls

    out.to_csv(BASE / 'dxy_london_patterns_vol.csv', index=False)
    print(f"  Enriched dataset saved: {BASE / 'dxy_london_patterns_vol.csv'}")
    print()

if __name__ == '__main__':
    main()
