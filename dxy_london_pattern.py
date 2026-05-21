"""
dxy_london_pattern.py
=====================
London Session Pattern Matching — DXY vs USDCAD and USDJPY
07:00–16:00 UTC window, 15m bars.

For every trading day in the dataset:
  1. Detects whether DXY showed ATTRACTION (retracement toward zone) or
     REVERSAL (impulse away from zone) during the London session.
  2. Checks whether USDCAD and USDJPY exhibited a matching directional pattern.
  3. Records indicator state (MACD, WPR, RSI, ADX, EMA alignment) for DXY
     and each pair at the London session open (07:00 bar).
  4. Outputs:
       • Daily pattern log
       • Match rate summary by pattern type
       • Indicator breakdown: which states predict a pattern match
       • Proposed filter rules (match% lift over baseline)
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ─── PATHS ────────────────────────────────────────────────────────────────────
BASE = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
FILE_MAP = {
    'DXY':    BASE / 'TVC_DXY, 15.csv',
    'USDCAD': BASE / 'FX_USDCAD, 15 (1).csv',
    'USDJPY': BASE / 'FX_USDJPY, 15 (1).csv',
}

# ─── PARAMETERS ───────────────────────────────────────────────────────────────
# Pair conversion: pts = (price_dist / FACTOR) * 10_000
PAIR_FACTOR = {'DXY': 0.01, 'USDCAD': 0.01, 'USDJPY': 1.0}
# Positive DXY correlation → pair moves same direction as DXY
PAIR_DIR    = {'USDCAD': 1, 'USDJPY': 1}

# Session (UTC hours, 15m bars)
LONDON_START_H, LONDON_END_H = 7, 16     # 07:00 inclusive to 16:00 exclusive
JAPAN_ZONE_H,   JAPAN_ZONE_M = 23, 45    # zone formed at 23:45 bar

# Zone formation (in pts, same as dxy_backtest.py)
ZONE_MIN_GAP = 30          # gap large enough to use open-gap zone vs candle body

# Pattern detection thresholds (pts, normalised via PAIR_FACTOR)
ATTRACT_MIN_START   = 100  # price must start ≥ this far from zone at London open
ATTRACT_APPROACH    = 0.40 # must close gap by ≥ this fraction to count as attraction
REVERSAL_ZONE_MAX   = 600  # price must be within this of zone edge at London open
REVERSAL_MIN_MOVE   = 150  # net move away from zone ≥ this many pts

# Indicator periods
EMA_FAST, EMA_SLOW = 20, 50
ADX_PERIOD         = 14
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
WPR_PERIOD         = 14
RSI_PERIOD         = 14

# ─── INDICATORS ───────────────────────────────────────────────────────────────
def ema(s, n):   return s.ewm(span=n, adjust=False).mean()
def rsi(s, n=14):
    d = s.diff(); g = d.clip(lower=0); lo = (-d).clip(lower=0)
    ag = g.ewm(com=n-1, adjust=False).mean()
    al = lo.ewm(com=n-1, adjust=False).mean()
    return 100 - 100 / (1 + ag / al.replace(0, np.nan))

def macd_hist(s, f=12, sl=26, sg=9):
    ml = ema(s, f) - ema(s, sl)
    return ml - ema(ml, sg)

def wpr(c, h, l, n=14):
    hi = h.rolling(n).max(); lo = l.rolling(n).min()
    rng = hi - lo
    return np.where(rng > 0, (hi - c) / rng * -100, -50.0)

def wilder_sum(s, n):
    res = np.zeros(len(s)); arr = np.nan_to_num(s.values)
    res[n-1] = arr[:n].sum()
    for i in range(n, len(s)):
        res[i] = res[i-1] - res[i-1]/n + arr[i]
    return pd.Series(res, index=s.index)

def wilder_mean(s, n):
    res = np.full(len(s), np.nan); arr = np.nan_to_num(s.values)
    seed = 2*n - 1
    if seed >= len(s): return pd.Series(res, index=s.index)
    res[seed] = arr[n:seed+1].mean()
    for i in range(seed+1, len(s)):
        res[i] = (res[i-1]*(n-1) + arr[i]) / n
    return pd.Series(res, index=s.index)

def calc_adx(df, n=14):
    ph = df['high'].shift(1); pl = df['low'].shift(1); pc = df['close'].shift(1)
    pdm = np.where((df['high']-ph)>(pl-df['low']), np.maximum(df['high']-ph,0), 0)
    mdm = np.where((pl-df['low'])>(df['high']-ph), np.maximum(pl-df['low'],0), 0)
    tr  = pd.concat([df['high']-df['low'],(df['high']-pc).abs(),(df['low']-pc).abs()],axis=1).max(axis=1)
    tr_s  = wilder_sum(pd.Series(tr.values,  index=df.index), n)
    pdm_s = wilder_sum(pd.Series(pdm,        index=df.index), n)
    mdm_s = wilder_sum(pd.Series(mdm,        index=df.index), n)
    dip = np.where(tr_s>0, pdm_s/tr_s*100, 0)
    dim = np.where(tr_s>0, mdm_s/tr_s*100, 0)
    dx  = np.where((dip+dim)>0, np.abs(dip-dim)/(dip+dim)*100, 0)
    return wilder_mean(pd.Series(dx, index=df.index), n)

def calc_4h_adx(df, n=14):
    dt = df.set_index(pd.to_datetime(df['time'], utc=True))
    df4 = dt[['open','high','low','close']].resample('4h').agg(
          {'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df4['adx'] = calc_adx(df4, n)
    def fl(ts): return int(ts.timestamp()//(4*3600))*(4*3600)
    amap = {fl(ts): row['adx'] for ts,row in df4.iterrows()}
    ts15 = pd.to_datetime(df['time'], utc=True)
    return pd.Series([amap.get(fl(t), np.nan) for t in ts15], index=df.index)

def add_indicators(df):
    df = df.copy()
    df['ema_fast'] = ema(df['close'], EMA_FAST)
    df['ema_slow'] = ema(df['close'], EMA_SLOW)
    df['macd_h']   = macd_hist(df['close'], MACD_FAST, MACD_SLOW, MACD_SIG)
    df['wpr']      = wpr(df['close'], df['high'], df['low'], WPR_PERIOD)
    df['rsi']      = rsi(df['close'], RSI_PERIOD)
    df['adx_4h']   = calc_4h_adx(df, ADX_PERIOD)
    return df

# ─── DATA LOADING ─────────────────────────────────────────────────────────────
def load(sym):
    df = pd.read_csv(FILE_MAP[sym])
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    # Keep only OHLC columns we need (some exports have extra cols)
    df = df[['time','open','high','low','close']].copy()
    df = add_indicators(df)
    return df

# ─── ZONE FORMATION (mirrors dxy_backtest.form_zone) ─────────────────────────
def form_zone(df, i):
    """Returns (zone_top, zone_bottom, is_bullish_zone) for 23:45 bar at index i."""
    if i < 1: return None, None, None
    prev_body = abs(df.at[i-1,'close'] - df.at[i-1,'open']) * 10000
    prior_c   = df.at[i-2,'close'] if (prev_body < 10 and i >= 2) else df.at[i-1,'close']
    j_open    = df.at[i,'open']
    j_close   = df.at[i,'close']
    gap       = abs(prior_c - j_open) * 10000
    if gap >= ZONE_MIN_GAP:
        zt, zb  = max(prior_c, j_open), min(prior_c, j_open)
        bull    = j_open > prior_c
    else:
        zt, zb  = max(j_open, j_close), min(j_open, j_close)
        bull    = j_close > j_open
    if abs(zt - zb) * 10000 < 1:
        zt = max(j_open, j_close) + 0.001
        zb = min(j_open, j_close)
    return zt, zb, bull

def pts(price_dist, sym):
    """Convert price distance to normalised pts."""
    return (abs(price_dist) / PAIR_FACTOR[sym]) * 10000

# ─── ZONE LOOKUP ──────────────────────────────────────────────────────────────
def build_zone_map(df):
    """Map each trading date → (zone_top, zone_bottom, zone_bull) from 23:45 bar."""
    z = {}
    mask_2345 = (df['time'].dt.hour == JAPAN_ZONE_H) & (df['time'].dt.minute == JAPAN_ZONE_M)
    for i in df.index[mask_2345]:
        try:
            zt, zb, zb_bull = form_zone(df, i)
        except Exception:
            continue
        if zt is None: continue
        # This zone applies to the next calendar day (London session the following day)
        date = (df.at[i,'time'] + pd.Timedelta(hours=1)).date()
        z[date] = (zt, zb, zb_bull)
    return z

# ─── LONDON SESSION SLICER ────────────────────────────────────────────────────
def london_bars(df, date):
    """Return 15m bars for the London session of `date` (07:00–15:45 UTC)."""
    mask = (
        (df['time'].dt.date == date) &
        (df['time'].dt.hour >= LONDON_START_H) &
        (df['time'].dt.hour < LONDON_END_H)
    )
    return df[mask].reset_index(drop=True)

# ─── PATTERN CLASSIFIER ───────────────────────────────────────────────────────
def classify_pattern(bars, zone_top, zone_bot, sym):
    """
    Classifies the London session pattern for one instrument on one day.

    Returns dict with keys:
      pattern     : 'ATTRACTION' | 'REVERSAL' | 'NONE'
      direction   : 'BULL' | 'BEAR' | None
      open_price  : price at 07:00 bar close
      open_dist   : distance (pts) from open price to nearest zone edge
      open_side   : 'ABOVE' | 'BELOW' | 'IN_ZONE'
      net_pts     : net session move (pts, positive = price went up)
      approach    : closest approach to zone during session (pts from nearest edge)
      impulse     : largest single bar range during session (pts)
      session_range: full session high-low (pts)
    """
    if bars.empty or zone_top is None:
        return dict(pattern='NO_DATA', direction=None, open_price=np.nan,
                    open_dist=np.nan, open_side=None, net_pts=np.nan,
                    approach=np.nan, impulse=np.nan, session_range=np.nan)

    o_price = bars.at[0,'close']
    s_high  = bars['high'].max()
    s_low   = bars['low'].min()
    s_close = bars.at[len(bars)-1,'close']
    net     = pts(s_close - o_price, sym)
    s_range = pts(s_high - s_low, sym)

    # Closest approach to zone during session (0 = touched zone)
    if s_high >= zone_bot and s_low <= zone_top:
        approach = 0.0   # session intersected the zone
    elif s_high < zone_bot:
        approach = pts(zone_bot - s_high, sym)   # below zone, how close to zone bottom
    else:
        approach = pts(s_low - zone_top, sym)    # above zone, how close to zone top

    # Position at London open
    if o_price > zone_top:
        open_side  = 'ABOVE'
        open_dist  = pts(o_price - zone_top, sym)
    elif o_price < zone_bot:
        open_side  = 'BELOW'
        open_dist  = pts(zone_bot - o_price, sym)
    else:
        open_side  = 'IN_ZONE'
        open_dist  = 0.0

    # Largest single candle range
    impulse = pts((bars['high'] - bars['low']).max(), sym)

    # ── PATTERN CLASSIFICATION ─────────────────────────────────────────────
    pattern   = 'NONE'
    direction = None

    # ATTRACTION: started far from zone, London session moved toward it
    if open_dist >= ATTRACT_MIN_START:
        # fraction of gap closed = (open_dist - approach) / open_dist
        gap_closed = (open_dist - approach) / open_dist if open_dist > 0 else 0
        if gap_closed >= ATTRACT_APPROACH:
            pattern = 'ATTRACTION'
            direction = 'BEAR' if open_side == 'ABOVE' else 'BULL'

    # REVERSAL: started near zone, session launched price away
    if pattern == 'NONE' and open_dist <= REVERSAL_ZONE_MAX:
        if open_side == 'ABOVE':
            # bullish reversal from zone top — price moved up
            if net >= REVERSAL_MIN_MOVE:
                pattern   = 'REVERSAL'
                direction = 'BULL'
            elif net <= -REVERSAL_MIN_MOVE:
                pattern   = 'REVERSAL'
                direction = 'BEAR'
        elif open_side == 'BELOW':
            if net <= -REVERSAL_MIN_MOVE:
                pattern   = 'REVERSAL'
                direction = 'BEAR'
            elif net >= REVERSAL_MIN_MOVE:
                pattern   = 'REVERSAL'
                direction = 'BULL'
        elif open_side == 'IN_ZONE':
            if net >= REVERSAL_MIN_MOVE:
                pattern   = 'REVERSAL'
                direction = 'BULL'
            elif net <= -REVERSAL_MIN_MOVE:
                pattern   = 'REVERSAL'
                direction = 'BEAR'

    return dict(pattern=pattern, direction=direction,
                open_price=o_price, open_dist=open_dist, open_side=open_side,
                net_pts=net, approach=approach, impulse=impulse,
                session_range=s_range)

# ─── INDICATOR SNAPSHOT ───────────────────────────────────────────────────────
def indicator_snap(df, date, sym):
    """Return indicator values at the 07:00 London open bar for a given date."""
    mask = (df['time'].dt.date == date) & (df['time'].dt.hour == LONDON_START_H) & (df['time'].dt.minute == 0)
    rows = df[mask]
    if rows.empty:
        # fallback: first London bar of the day
        mask2 = (df['time'].dt.date == date) & (df['time'].dt.hour == LONDON_START_H)
        rows = df[mask2]
    if rows.empty:
        return {}
    i = rows.index[0]
    r = df.loc[i]
    # EMA alignment
    ema_bull = (r['close'] > r['ema_fast']) and (r['ema_fast'] > r['ema_slow'])
    ema_bear = (r['close'] < r['ema_fast']) and (r['ema_fast'] < r['ema_slow'])
    ema_align = 'BULL' if ema_bull else ('BEAR' if ema_bear else 'MIXED')
    # MACD
    macd_pos  = r['macd_h'] > 0
    # WPR zones
    wpr_os    = r['wpr'] <= -80   # oversold
    wpr_ob    = r['wpr'] >= -20   # overbought
    # RSI zones
    rsi_os    = r['rsi'] <= 35
    rsi_ob    = r['rsi'] >= 65
    # ADX trend
    adx_trend = r['adx_4h'] >= 25 if not np.isnan(r['adx_4h']) else False
    return {
        f'{sym}_macd_h'    : round(r['macd_h'], 6),
        f'{sym}_macd_pos'  : macd_pos,
        f'{sym}_wpr'       : round(float(r['wpr']), 1),
        f'{sym}_wpr_os'    : bool(wpr_os),
        f'{sym}_wpr_ob'    : bool(wpr_ob),
        f'{sym}_rsi'       : round(r['rsi'], 1),
        f'{sym}_rsi_os'    : bool(rsi_os),
        f'{sym}_rsi_ob'    : bool(rsi_ob),
        f'{sym}_adx_4h'    : round(r['adx_4h'], 1) if not np.isnan(r['adx_4h']) else np.nan,
        f'{sym}_adx_trend' : adx_trend,
        f'{sym}_ema_align' : ema_align,
        f'{sym}_ema_bull'  : ema_bull,
        f'{sym}_ema_bear'  : ema_bear,
    }

# ─── MATCH LOGIC ──────────────────────────────────────────────────────────────
def is_match(dxy_pat, dxy_dir, pair_pat, pair_dir_setting):
    """
    Returns True if pair pattern type matches DXY and direction is correlated.
    pair_dir_setting: +1 means pair moves same direction as DXY.
    """
    if dxy_pat not in ('ATTRACTION', 'REVERSAL'): return False
    if pair_pat != dxy_pat: return False
    if dxy_dir is None or pair_pat == 'NONE': return False
    # Expected pair direction given DXY direction and correlation
    expected = dxy_dir   # +1 correlation: same direction
    return pair_pat == dxy_pat and (
        (expected == 'BULL' and pair_dir_setting == 1) or
        (expected == 'BEAR' and pair_dir_setting == 1)
    ) or (pair_pat == dxy_pat)   # simplified: same pattern type + any direction checked below

def directions_aligned(dxy_dir, pair_dir, pair_dir_setting):
    """True if pair's direction matches the DXY direction given correlation sign."""
    if dxy_dir is None or pair_dir is None: return False
    if pair_dir_setting == 1:
        return dxy_dir == pair_dir    # positive correlation → same direction
    else:
        return dxy_dir != pair_dir    # negative correlation → opposite

# ─── INDICATOR CORRELATION REPORT ─────────────────────────────────────────────
def indicator_report(days_df, pair, base_match_rate):
    """
    For each binary indicator flag, compute:
      - count when flag is True
      - match rate when flag is True
      - lift over baseline
    """
    bool_cols = [c for c in days_df.columns
                 if c.endswith(('_pos', '_os', '_ob', '_trend', '_bull', '_bear'))
                 and (c.startswith('DXY_') or c.startswith(f'{pair}_'))]

    rows = []
    for col in bool_cols:
        sub = days_df[days_df[col] == True]
        if len(sub) < 3: continue
        mr = sub[f'{pair}_match'].mean() * 100
        rows.append({
            'Indicator'  : col,
            'N_days'     : len(sub),
            'Match_pct'  : round(mr, 1),
            'Lift_pp'    : round(mr - base_match_rate, 1),
        })
    return pd.DataFrame(rows).sort_values('Lift_pp', ascending=False)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print("Loading data and computing indicators...")
    data = {sym: load(sym) for sym in FILE_MAP}

    print("Building zone maps...")
    zones = {sym: build_zone_map(data[sym]) for sym in data}

    # All trading dates that appear in both DXY and pairs
    all_dates = sorted(set(data['DXY']['time'].dt.date) &
                       set(data['USDCAD']['time'].dt.date) &
                       set(data['USDJPY']['time'].dt.date))

    print(f"Scanning {len(all_dates)} trading days for London session patterns...\n")

    records = []

    for date in all_dates:
        # Get zones for this date
        dxy_zone   = zones['DXY'].get(date,    (None, None, None))
        cad_zone   = zones['USDCAD'].get(date, (None, None, None))
        jpy_zone   = zones['USDJPY'].get(date, (None, None, None))

        if dxy_zone[0] is None: continue

        # London bars for each instrument
        dxy_bars = london_bars(data['DXY'],    date)
        cad_bars = london_bars(data['USDCAD'], date)
        jpy_bars = london_bars(data['USDJPY'], date)

        if dxy_bars.empty: continue

        # Pattern classification
        dxy_p = classify_pattern(dxy_bars, dxy_zone[0], dxy_zone[1], 'DXY')
        cad_p = classify_pattern(cad_bars, cad_zone[0], cad_zone[1], 'USDCAD') if cad_zone[0] else {'pattern':'NO_DATA','direction':None}
        jpy_p = classify_pattern(jpy_bars, jpy_zone[0], jpy_zone[1], 'USDJPY') if jpy_zone[0] else {'pattern':'NO_DATA','direction':None}

        # Indicator snapshots
        dxy_ind  = indicator_snap(data['DXY'],    date, 'DXY')
        cad_ind  = indicator_snap(data['USDCAD'], date, 'USDCAD')
        jpy_ind  = indicator_snap(data['USDJPY'], date, 'USDJPY')

        # Match assessment (pattern type AND direction aligned)
        cad_match = (cad_p['pattern'] == dxy_p['pattern'] and
                     dxy_p['pattern'] in ('ATTRACTION','REVERSAL') and
                     directions_aligned(dxy_p['direction'], cad_p['direction'], PAIR_DIR['USDCAD']))

        jpy_match = (jpy_p['pattern'] == dxy_p['pattern'] and
                     dxy_p['pattern'] in ('ATTRACTION','REVERSAL') and
                     directions_aligned(dxy_p['direction'], jpy_p['direction'], PAIR_DIR['USDJPY']))

        rec = {
            'date'           : date,
            'DXY_pattern'    : dxy_p['pattern'],
            'DXY_direction'  : dxy_p['direction'],
            'DXY_open_dist'  : round(dxy_p['open_dist'], 0) if dxy_p['open_dist'] else np.nan,
            'DXY_open_side'  : dxy_p['open_side'],
            'DXY_net_pts'    : round(dxy_p['net_pts'], 0) if dxy_p['net_pts'] else np.nan,
            'DXY_approach'   : round(dxy_p['approach'], 0) if dxy_p['approach'] else np.nan,
            'DXY_impulse'    : round(dxy_p['impulse'], 0) if dxy_p['impulse'] else np.nan,

            'USDCAD_pattern' : cad_p['pattern'],
            'USDCAD_dir'     : cad_p['direction'],
            'USDCAD_net_pts' : round(cad_p['net_pts'], 0) if cad_p.get('net_pts') else np.nan,
            'USDCAD_match'   : cad_match,

            'USDJPY_pattern' : jpy_p['pattern'],
            'USDJPY_dir'     : jpy_p['direction'],
            'USDJPY_net_pts' : round(jpy_p['net_pts'], 0) if jpy_p.get('net_pts') else np.nan,
            'USDJPY_match'   : jpy_match,
        }
        rec.update(dxy_ind)
        rec.update(cad_ind)
        rec.update(jpy_ind)
        records.append(rec)

    days_df = pd.DataFrame(records)
    days_df.to_csv(BASE / 'dxy_london_patterns.csv', index=False)

    # ─── SECTION 1: OVERVIEW ─────────────────────────────────────────────────
    pat_counts = days_df['DXY_pattern'].value_counts()
    total = len(days_df)
    act_days = days_df[days_df['DXY_pattern'].isin(['ATTRACTION','REVERSAL'])]

    print("=" * 72)
    print("  1. DXY LONDON SESSION OVERVIEW")
    print("=" * 72)
    print(f"  Total trading days scanned : {total}")
    print(f"  Days with clear DXY pattern: {len(act_days)}  "
          f"({len(act_days)/total*100:.0f}%)")
    print()
    for p in ['ATTRACTION','REVERSAL','NONE','NO_DATA']:
        n = pat_counts.get(p, 0)
        if n == 0: continue
        pct = n / total * 100
        # direction split
        sub = days_df[days_df['DXY_pattern'] == p]
        bulls = (sub['DXY_direction'] == 'BULL').sum()
        bears = (sub['DXY_direction'] == 'BEAR').sum()
        print(f"  {p:<12} : {n:3d} days ({pct:4.1f}%)   BULL={bulls}  BEAR={bears}")
    print()

    # ─── SECTION 2: PAIR MATCH RATES ─────────────────────────────────────────
    print("=" * 72)
    print("  2. PATTERN MATCH RATES  (pair shows same pattern type + direction)")
    print("=" * 72)
    for pair in ['USDCAD', 'USDJPY']:
        print(f"\n  {pair}")
        print(f"  {'Pattern':<12}  {'DXY days':>8}  {'Matches':>7}  {'Match%':>7}")
        print(f"  {'-'*42}")
        for p in ['ATTRACTION', 'REVERSAL']:
            sub  = act_days[act_days['DXY_pattern'] == p]
            if sub.empty: continue
            mats = sub[f'{pair}_match'].sum()
            mr   = mats / len(sub) * 100
            print(f"  {p:<12}  {len(sub):>8}  {mats:>7}  {mr:>6.1f}%")
        # Overall on active pattern days
        mats = act_days[f'{pair}_match'].sum()
        mr   = mats / len(act_days) * 100 if len(act_days) > 0 else 0
        print(f"  {'OVERALL':<12}  {len(act_days):>8}  {mats:>7}  {mr:>6.1f}%")

    print()

    # ─── SECTION 3: DATE-BY-DATE TABLE ───────────────────────────────────────
    print("=" * 72)
    print("  3. DATE-BY-DATE  (active pattern days only)")
    print("=" * 72)
    hdr = f"  {'Date':<12} {'DXY Pat':<12} {'DXY Dir':<7} {'DXY net':>7} {'CAD Pat':<12} {'CAD':>5} {'CAD net':>7} {'JPY Pat':<12} {'JPY':>5} {'JPY net':>7}"
    print(hdr)
    print(f"  {'-'*92}")
    for _, row in act_days.sort_values('date').iterrows():
        cad_tick = 'MATCH' if row['USDCAD_match'] else '  -  '
        jpy_tick = 'MATCH' if row['USDJPY_match'] else '  -  '
        print(f"  {str(row['date']):<12} {row['DXY_pattern']:<12} {str(row['DXY_direction']):<7} "
              f"{row['DXY_net_pts']:>7.0f} "
              f"{row['USDCAD_pattern']:<12} {cad_tick:>5} {row['USDCAD_net_pts']:>7.0f} "
              f"{row['USDJPY_pattern']:<12} {jpy_tick:>5} {row['USDJPY_net_pts']:>7.0f}")
    print()

    # ─── SECTION 4: INDICATOR ANALYSIS ───────────────────────────────────────
    print("=" * 72)
    print("  4. INDICATOR ANALYSIS AT LONDON OPEN (07:00 bar)")
    print("     Which indicator states predict a pattern MATCH?")
    print("=" * 72)

    for pair in ['USDCAD', 'USDJPY']:
        base_rate = act_days[f'{pair}_match'].mean() * 100 if len(act_days) > 0 else 0
        print(f"\n  {pair}  —  Baseline match rate: {base_rate:.1f}%")
        print(f"  {'Indicator':<28}  {'N':>5}  {'Match%':>7}  {'Lift':>7}")
        print(f"  {'-'*55}")

        ind_df = indicator_report(act_days, pair, base_rate)
        if ind_df.empty:
            print("  (insufficient data)")
            continue
        for _, r in ind_df.iterrows():
            bar = '+' if r['Lift_pp'] > 0 else ''
            print(f"  {r['Indicator']:<28}  {r['N_days']:>5}  {r['Match_pct']:>6.1f}%  "
                  f"{bar}{r['Lift_pp']:>+5.1f}pp")

    print()

    # ─── SECTION 5: COMBINED FILTER TEST ─────────────────────────────────────
    print("=" * 72)
    print("  5. TOP COMBINED FILTERS  (2-indicator combinations)")
    print("=" * 72)

    for pair in ['USDCAD', 'USDJPY']:
        base_rate = act_days[f'{pair}_match'].mean() * 100 if len(act_days) > 0 else 0
        bool_cols = [c for c in act_days.columns
                     if c.endswith(('_pos','_os','_ob','_trend','_bull','_bear'))
                     and (c.startswith('DXY_') or c.startswith(f'{pair}_'))]

        combos = []
        for i in range(len(bool_cols)):
            for j in range(i+1, len(bool_cols)):
                c1, c2 = bool_cols[i], bool_cols[j]
                sub = act_days[(act_days[c1]==True) & (act_days[c2]==True)]
                if len(sub) < 4: continue
                mr = sub[f'{pair}_match'].mean() * 100
                combos.append({'filter': f"{c1} & {c2}", 'N': len(sub),
                               'match_pct': mr, 'lift': mr - base_rate})
        if combos:
            cdf = pd.DataFrame(combos).sort_values('lift', ascending=False).head(10)
            print(f"\n  {pair}  —  Top 10 two-indicator combinations:")
            print(f"  {'Filter':<56}  {'N':>5}  {'Match%':>7}  {'Lift':>7}")
            print(f"  {'-'*80}")
            for _, r in cdf.iterrows():
                print(f"  {r['filter']:<56}  {r['N']:>5}  {r['match_pct']:>6.1f}%  "
                      f"{r['lift']:>+6.1f}pp")

    print()

    # ─── SECTION 6: INDICATOR VALUE DISTRIBUTIONS ────────────────────────────
    print("=" * 72)
    print("  6. INDICATOR VALUE SUMMARY  (mean ± std, match vs no-match)")
    print("=" * 72)

    cont_cols_dxy  = ['DXY_macd_h','DXY_wpr','DXY_rsi','DXY_adx_4h']
    cont_cols_pair = {
        'USDCAD': ['USDCAD_macd_h','USDCAD_wpr','USDCAD_rsi','USDCAD_adx_4h'],
        'USDJPY': ['USDJPY_macd_h','USDJPY_wpr','USDJPY_rsi','USDJPY_adx_4h'],
    }

    for pair in ['USDCAD', 'USDJPY']:
        matched     = act_days[act_days[f'{pair}_match'] == True]
        not_matched = act_days[act_days[f'{pair}_match'] == False]
        cols = cont_cols_dxy + cont_cols_pair[pair]
        print(f"\n  {pair}  (matched={len(matched)}, not-matched={len(not_matched)})")
        print(f"  {'Indicator':<22}  {'Match mean':>11}  {'NoMatch mean':>12}  {'Diff':>8}")
        print(f"  {'-'*60}")
        for col in cols:
            if col not in act_days.columns: continue
            m_mean = matched[col].mean()     if not matched.empty else np.nan
            n_mean = not_matched[col].mean() if not not_matched.empty else np.nan
            diff   = m_mean - n_mean
            lbl    = col.replace('USDCAD_','').replace('USDJPY_','').replace('DXY_','DXY:')
            print(f"  {lbl:<22}  {m_mean:>11.3f}  {n_mean:>12.3f}  {diff:>+8.3f}")

    print()
    print(f"  Full day-by-day log saved: {BASE / 'dxy_london_patterns.csv'}")
    print()

if __name__ == '__main__':
    main()
