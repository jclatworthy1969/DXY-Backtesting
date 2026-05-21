"""
dxy_london_pattern_v2.py
========================
London Session Pattern Matching ? DXY vs 4 pairs
  Positive correlation (move same direction as DXY): USDCAD, USDJPY
  Negative correlation (move opposite to DXY):       EURUSD, XAUUSD

Validates whether the ADX + WPR overbought/oversold indicator findings
from the USDCAD/USDJPY analysis hold for the inverse pairs.

Expected hypothesis:
  ? DXY WPR oversold  ? DXY low, about to attract/reverse upward
    ? USDCAD/USDJPY should follow UP     (positive corr ? same dir)
    ? EURUSD/XAUUSD  should follow DOWN  (negative corr ? opposite dir)
    In both cases ? high MATCH rate when DXY WPR ? -80

  ? For each pair's own WPR:
    USDCAD/USDJPY  : pair WPR overbought (?-20) ? pair at high, DXY also at high
    EURUSD/XAUUSD  : pair WPR oversold   (?-80)  ? pair at low = DXY at high
    Both ? should predict pattern match
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ??? PATHS ????????????????????????????????????????????????????????????????????
BASE = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

FILE_MAP = {
    'DXY':    BASE / 'TVC_DXY, 15.csv',
    'USDCAD': BASE / 'FX_USDCAD, 15 (1).csv',
    'USDJPY': BASE / 'FX_USDJPY, 15 (1).csv',
    'EURUSD': BASE / 'FX_EURUSD, 15 (1).csv',
    'XAUUSD': BASE / 'FX_XAUUSD, 15 (1).csv',
}

# ??? PARAMETERS ???????????????????????????????????????????????????????????????
# pts = (price_dist / FACTOR) * 10_000   ? normalises to DXY-equivalent points
PAIR_FACTOR = {
    'DXY':    0.01,
    'USDCAD': 0.01,
    'USDJPY': 1.0,
    'EURUSD': 0.01,
    'XAUUSD': 100.0,
}
# +1 = moves same direction as DXY, -1 = moves opposite direction
PAIR_DIR = {
    'USDCAD': +1,
    'USDJPY': +1,
    'EURUSD': -1,
    'XAUUSD': -1,
}
PAIRS = list(PAIR_DIR.keys())   # ['USDCAD','USDJPY','EURUSD','XAUUSD']

# Session (UTC)
LONDON_START_H, LONDON_END_H = 7, 16
JAPAN_ZONE_H,   JAPAN_ZONE_M = 23, 45

# Zone formation
ZONE_MIN_GAP = 30

# Pattern thresholds (normalised pts)
ATTRACT_MIN_START   = 100
ATTRACT_APPROACH    = 0.40
REVERSAL_ZONE_MAX   = 600
REVERSAL_MIN_MOVE   = 150

# Indicators
EMA_FAST, EMA_SLOW               = 20, 50
MACD_FAST, MACD_SLOW, MACD_SIG   = 12, 26, 9
WPR_PERIOD, RSI_PERIOD, ADX_PER  = 14, 14, 14

# ??? INDICATORS ???????????????????????????????????????????????????????????????
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff(); g = d.clip(lower=0); lo = (-d).clip(lower=0)
    ag = g.ewm(com=n-1, adjust=False).mean()
    al = lo.ewm(com=n-1, adjust=False).mean()
    return 100 - 100 / (1 + ag / al.replace(0, np.nan))

def macd_hist(s, f=12, sl=26, sg=9):
    ml = ema(s, f) - ema(s, sl)
    return ml - ema(ml, sg)

def wpr(c, h, l, n=14):
    hi = h.rolling(n).max(); lo = l.rolling(n).min(); rng = hi - lo
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
    amap = {fl(ts): row['adx'] for ts, row in df4.iterrows()}
    ts15 = pd.to_datetime(df['time'], utc=True)
    return pd.Series([amap.get(fl(t), np.nan) for t in ts15], index=df.index)

def add_indicators(df):
    df = df.copy()
    df['ema_fast'] = ema(df['close'], EMA_FAST)
    df['ema_slow'] = ema(df['close'], EMA_SLOW)
    df['macd_h']   = macd_hist(df['close'], MACD_FAST, MACD_SLOW, MACD_SIG)
    df['wpr']      = wpr(df['close'], df['high'], df['low'], WPR_PERIOD)
    df['rsi']      = rsi(df['close'], RSI_PERIOD)
    df['adx_4h']   = calc_4h_adx(df, ADX_PER)
    return df

# ??? DATA LOADING ?????????????????????????????????????????????????????????????
def load(sym):
    df = pd.read_csv(FILE_MAP[sym])
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    df = df[['time','open','high','low','close']].copy()
    return add_indicators(df)

# ??? ZONE FORMATION ???????????????????????????????????????????????????????????
def form_zone(df, i):
    if i < 1: return None, None, None
    prev_body = abs(df.at[i-1,'close'] - df.at[i-1,'open']) * 10000
    prior_c   = df.at[i-2,'close'] if (prev_body < 10 and i >= 2) else df.at[i-1,'close']
    j_open    = df.at[i,'open']
    j_close   = df.at[i,'close']
    gap       = abs(prior_c - j_open) * 10000
    if gap >= ZONE_MIN_GAP:
        zt, zb = max(prior_c, j_open), min(prior_c, j_open)
    else:
        zt, zb = max(j_open, j_close), min(j_open, j_close)
    if abs(zt - zb) * 10000 < 1:
        zt = max(j_open, j_close) + 0.001
        zb = min(j_open, j_close)
    bull = j_close > j_open if gap < ZONE_MIN_GAP else j_open > prior_c
    return zt, zb, bull

def pts(price_dist, sym):
    return (abs(price_dist) / PAIR_FACTOR[sym]) * 10000

def build_zone_map(df):
    z = {}
    mask = (df['time'].dt.hour == JAPAN_ZONE_H) & (df['time'].dt.minute == JAPAN_ZONE_M)
    for i in df.index[mask]:
        try:
            zt, zb, zb_bull = form_zone(df, i)
        except Exception:
            continue
        if zt is None: continue
        date = (df.at[i,'time'] + pd.Timedelta(hours=1)).date()
        z[date] = (zt, zb, zb_bull)
    return z

# ??? SESSION SLICER ???????????????????????????????????????????????????????????
def london_bars(df, date):
    mask = (df['time'].dt.date == date) & \
           (df['time'].dt.hour >= LONDON_START_H) & \
           (df['time'].dt.hour < LONDON_END_H)
    return df[mask].reset_index(drop=True)

# ??? PATTERN CLASSIFIER ???????????????????????????????????????????????????????
def classify_pattern(bars, zone_top, zone_bot, sym):
    if bars.empty or zone_top is None:
        return dict(pattern='NO_DATA', direction=None, open_price=np.nan,
                    open_dist=np.nan, open_side=None, net_pts=np.nan,
                    approach=np.nan, impulse=np.nan, session_range=np.nan)
    o_price = bars.at[0,'close']
    s_high  = bars['high'].max()
    s_low   = bars['low'].min()
    s_close = bars.at[len(bars)-1,'close']
    net     = pts(s_close - o_price, sym) * (1 if s_close >= o_price else -1)
    net     = (s_close - o_price) / PAIR_FACTOR[sym] * 10000
    s_range = pts(s_high - s_low, sym)

    if s_high >= zone_bot and s_low <= zone_top:
        approach = 0.0
    elif s_high < zone_bot:
        approach = pts(zone_bot - s_high, sym)
    else:
        approach = pts(s_low - zone_top, sym)

    if o_price > zone_top:
        open_side  = 'ABOVE'; open_dist = pts(o_price - zone_top, sym)
    elif o_price < zone_bot:
        open_side  = 'BELOW'; open_dist = pts(zone_bot - o_price, sym)
    else:
        open_side  = 'IN_ZONE'; open_dist = 0.0

    impulse   = pts((bars['high'] - bars['low']).max(), sym)
    pattern   = 'NONE'
    direction = None

    if open_dist >= ATTRACT_MIN_START:
        gap_closed = (open_dist - approach) / open_dist if open_dist > 0 else 0
        if gap_closed >= ATTRACT_APPROACH:
            pattern   = 'ATTRACTION'
            direction = 'BEAR' if open_side == 'ABOVE' else 'BULL'

    if pattern == 'NONE' and open_dist <= REVERSAL_ZONE_MAX:
        if open_side == 'ABOVE':
            if net >= REVERSAL_MIN_MOVE:
                pattern, direction = 'REVERSAL', 'BULL'
            elif net <= -REVERSAL_MIN_MOVE:
                pattern, direction = 'REVERSAL', 'BEAR'
        elif open_side == 'BELOW':
            if net <= -REVERSAL_MIN_MOVE:
                pattern, direction = 'REVERSAL', 'BEAR'
            elif net >= REVERSAL_MIN_MOVE:
                pattern, direction = 'REVERSAL', 'BULL'
        elif open_side == 'IN_ZONE':
            if net >= REVERSAL_MIN_MOVE:
                pattern, direction = 'REVERSAL', 'BULL'
            elif net <= -REVERSAL_MIN_MOVE:
                pattern, direction = 'REVERSAL', 'BEAR'

    return dict(pattern=pattern, direction=direction, open_price=o_price,
                open_dist=open_dist, open_side=open_side, net_pts=net,
                approach=approach, impulse=impulse, session_range=s_range)

# ??? INDICATOR SNAPSHOT ???????????????????????????????????????????????????????
def indicator_snap(df, date, sym):
    mask = (df['time'].dt.date == date) & (df['time'].dt.hour == LONDON_START_H) & (df['time'].dt.minute == 0)
    rows = df[mask]
    if rows.empty:
        rows = df[(df['time'].dt.date == date) & (df['time'].dt.hour == LONDON_START_H)]
    if rows.empty:
        return {}
    r = df.loc[rows.index[0]]
    ema_bull  = bool((r['close'] > r['ema_fast']) and (r['ema_fast'] > r['ema_slow']))
    ema_bear  = bool((r['close'] < r['ema_fast']) and (r['ema_fast'] < r['ema_slow']))
    adx_trend = bool(r['adx_4h'] >= 25) if not np.isnan(r['adx_4h']) else False
    return {
        f'{sym}_macd_h'   : round(r['macd_h'], 6),
        f'{sym}_macd_pos' : bool(r['macd_h'] > 0),
        f'{sym}_wpr'      : round(float(r['wpr']), 1),
        f'{sym}_wpr_os'   : bool(r['wpr'] <= -80),
        f'{sym}_wpr_ob'   : bool(r['wpr'] >= -20),
        f'{sym}_rsi'      : round(r['rsi'], 1),
        f'{sym}_rsi_os'   : bool(r['rsi'] <= 35),
        f'{sym}_rsi_ob'   : bool(r['rsi'] >= 65),
        f'{sym}_adx_4h'   : round(r['adx_4h'], 1) if not np.isnan(r['adx_4h']) else np.nan,
        f'{sym}_adx_trend': adx_trend,
        f'{sym}_ema_bull' : ema_bull,
        f'{sym}_ema_bear' : ema_bear,
    }

# ??? MATCH LOGIC ??????????????????????????????????????????????????????????????
def directions_aligned(dxy_dir, pair_dir, corr):
    """True when the pair moved in the expected correlated direction."""
    if dxy_dir is None or pair_dir is None: return False
    return dxy_dir == pair_dir if corr == 1 else dxy_dir != pair_dir

# ??? INDICATOR REPORT ?????????????????????????????????????????????????????????
def indicator_report(act_df, pair, base_rate):
    bool_cols = [c for c in act_df.columns
                 if c.endswith(('_pos','_os','_ob','_trend','_bull','_bear'))
                 and (c.startswith('DXY_') or c.startswith(f'{pair}_'))]
    rows = []
    for col in bool_cols:
        sub = act_df[act_df[col] == True]
        if len(sub) < 3: continue
        mr = sub[f'{pair}_match'].mean() * 100
        rows.append({'Indicator': col, 'N': len(sub),
                     'Match%': round(mr,1), 'Lift': round(mr - base_rate, 1)})
    return pd.DataFrame(rows).sort_values('Lift', ascending=False)

# ??? MAIN ?????????????????????????????????????????????????????????????????????
def main():
    print("Loading data and computing indicators...")
    data  = {sym: load(sym) for sym in FILE_MAP}
    zones = {sym: build_zone_map(data[sym]) for sym in data}
    print("Zones built.")

    common = set(data['DXY']['time'].dt.date)
    for p in PAIRS:
        common &= set(data[p]['time'].dt.date)
    all_dates = sorted(common)
    print(f"Scanning {len(all_dates)} trading days (all 5 instruments present)...\n")

    records = []
    for date in all_dates:
        dxy_zone = zones['DXY'].get(date, (None, None, None))
        if dxy_zone[0] is None: continue
        dxy_bars = london_bars(data['DXY'], date)
        if dxy_bars.empty: continue

        dxy_p   = classify_pattern(dxy_bars, dxy_zone[0], dxy_zone[1], 'DXY')
        dxy_ind = indicator_snap(data['DXY'], date, 'DXY')

        rec = {
            'date'         : date,
            'DXY_pattern'  : dxy_p['pattern'],
            'DXY_direction': dxy_p['direction'],
            'DXY_open_dist': round(dxy_p['open_dist'], 0),
            'DXY_open_side': dxy_p['open_side'],
            'DXY_net_pts'  : round(dxy_p['net_pts'], 0),
            'DXY_approach' : round(dxy_p['approach'], 0),
        }
        rec.update(dxy_ind)

        for pair in PAIRS:
            pz = zones[pair].get(date, (None, None, None))
            pb = london_bars(data[pair], date)
            pp = classify_pattern(pb, pz[0], pz[1], pair) if pz[0] else \
                 {'pattern':'NO_DATA','direction':None,'net_pts':np.nan}
            pi = indicator_snap(data[pair], date, pair)

            match = (pp['pattern'] == dxy_p['pattern'] and
                     dxy_p['pattern'] in ('ATTRACTION','REVERSAL') and
                     directions_aligned(dxy_p['direction'], pp['direction'], PAIR_DIR[pair]))

            rec[f'{pair}_pattern'] = pp['pattern']
            rec[f'{pair}_dir']     = pp['direction']
            rec[f'{pair}_net_pts'] = round(pp['net_pts'], 0) if pp.get('net_pts') is not None else np.nan
            rec[f'{pair}_match']   = match
            rec.update(pi)

        records.append(rec)

    df = pd.DataFrame(records)
    df.to_csv(BASE / 'dxy_london_patterns_v2.csv', index=False)
    act = df[df['DXY_pattern'].isin(['ATTRACTION','REVERSAL'])].copy()

    # ??? SECTION 1: DXY OVERVIEW ??????????????????????????????????????????????
    pat_counts = df['DXY_pattern'].value_counts()
    total = len(df)
    print("=" * 76)
    print("  1. DXY LONDON SESSION OVERVIEW")
    print("=" * 76)
    print(f"  Total days: {total}   Active pattern days: {len(act)} ({len(act)/total*100:.0f}%)")
    for p in ['ATTRACTION','REVERSAL','NONE','NO_DATA']:
        n = pat_counts.get(p, 0)
        if not n: continue
        sub = df[df['DXY_pattern']==p]
        bulls = (sub['DXY_direction']=='BULL').sum()
        bears = (sub['DXY_direction']=='BEAR').sum()
        print(f"  {p:<12}: {n:3d} days ({n/total*100:4.1f}%)   BULL={bulls}  BEAR={bears}")
    print()

    # ??? SECTION 2: MATCH RATES ???????????????????????????????????????????????
    print("=" * 76)
    print("  2. PATTERN MATCH RATES  (same pattern type + correlated direction)")
    print("=" * 76)
    corr_labels = {p: f"(+corr)" if PAIR_DIR[p]==1 else "(-corr)" for p in PAIRS}
    hdr = f"  {'Pair':<10} {'Corr':<8} {'ATTRACT days':>12} {'ATTRACT%':>9} {'REVERSAL days':>13} {'REVERSAL%':>10} {'OVERALL%':>9}"
    print(hdr)
    print(f"  {'-'*75}")
    for pair in PAIRS:
        a_sub = act[act['DXY_pattern']=='ATTRACTION']
        r_sub = act[act['DXY_pattern']=='REVERSAL']
        a_mr  = a_sub[f'{pair}_match'].mean()*100 if len(a_sub) else 0
        r_mr  = r_sub[f'{pair}_match'].mean()*100 if len(r_sub) else 0
        o_mr  = act[f'{pair}_match'].mean()*100    if len(act) else 0
        print(f"  {pair:<10} {corr_labels[pair]:<8} {len(a_sub):>12}  {a_mr:>8.1f}%"
              f" {len(r_sub):>13}  {r_mr:>9.1f}%  {o_mr:>8.1f}%")
    print()

    # ??? SECTION 3: INDICATOR BREAKDOWN PER PAIR ?????????????????????????????
    print("=" * 76)
    print("  3. SINGLE INDICATOR BREAKDOWN AT LONDON OPEN")
    print("     (each row = when that indicator flag is TRUE on active-pattern days)")
    print("=" * 76)
    for pair in PAIRS:
        base = act[f'{pair}_match'].mean()*100
        rpt  = indicator_report(act, pair, base)
        corr = corr_labels[pair]
        print(f"\n  {pair} {corr}  ?  baseline {base:.1f}%")
        print(f"  {'Indicator':<30}  {'N':>5}  {'Match%':>7}  {'Lift':>7}")
        print(f"  {'-'*56}")
        for _, r in rpt.iterrows():
            print(f"  {r['Indicator']:<30}  {r['N']:>5}  {r['Match%']:>6.1f}%  {r['Lift']:>+6.1f}pp")
    print()

    # ??? SECTION 4: CROSS-PAIR VALIDATION TABLE ???????????????????????????????
    # The key test: does DXY_wpr_os lift match rates for ALL pairs (positive AND negative corr)?
    # And does the pair's OWN WPR in the expected extreme (ob for pos-corr, os for neg-corr) also lift?
    print("=" * 76)
    print("  4. CROSS-PAIR VALIDATION ? Hypothesis Test")
    print("     DXY WPR oversold should lift MATCH for ALL 4 pairs")
    print("     Pair WPR: pos-corr pairs -> overbought lifts; neg-corr pairs -> oversold lifts")
    print("=" * 76)

    key_filters = {
        'DXY_wpr_os'     : "DXY WPR <=-80 (DXY oversold)",
        'DXY_wpr_ob'     : "DXY WPR >=-20 (DXY overbought)",
        'DXY_adx_trend'  : "DXY 4H ADX >=25 (DXY trending)",
        'DXY_ema_bear'   : "DXY EMA bear alignment",
        'DXY_ema_bull'   : "DXY EMA bull alignment",
    }

    # Header
    pair_hdrs = ''.join([f"  {p:>10}" for p in PAIRS])
    print(f"\n  {'Filter':<40}" + pair_hdrs)
    print(f"  {'(N / match%)':<40}" + ''.join([f"  {corr_labels[p]:>10}" for p in PAIRS]))
    print(f"  {'-'*90}")

    # Baseline row
    base_row = ''.join([f"  {act[f'{p}_match'].mean()*100:>9.1f}%" for p in PAIRS])
    print(f"  {'Baseline (all active days)':<40}" + base_row)
    print(f"  {'N active days':<40}" + ''.join([f"  {len(act):>10}" for _ in PAIRS]))
    print()

    for col, label in key_filters.items():
        sub = act[act[col]==True] if col in act.columns else act.iloc[0:0]
        n   = len(sub)
        if n < 3:
            vals = ''.join([f"  {'n/a':>10}" for _ in PAIRS])
        else:
            vals = ''.join([f"  {sub[f'{p}_match'].mean()*100:>9.1f}%" for p in PAIRS])
        print(f"  {label:<40}  N={n:>3}" + vals)

    print()

    # Also test each pair's own WPR extreme
    print("  Pair's own WPR extreme (expected direction per correlation):")
    print(f"  {'pos-corr ? WPR_ob  /  neg-corr ? WPR_os':<40}" + pair_hdrs)
    print(f"  {'-'*90}")
    for pair in PAIRS:
        own_col   = f'{pair}_wpr_ob' if PAIR_DIR[pair]==1 else f'{pair}_wpr_os'
        other_col = f'{pair}_wpr_os' if PAIR_DIR[pair]==1 else f'{pair}_wpr_ob'
        label_own   = f"{pair} WPR expected extreme"
        label_other = f"{pair} WPR opposite extreme"
        for col, lbl in [(own_col, label_own), (other_col, label_other)]:
            sub = act[act[col]==True] if col in act.columns else act.iloc[0:0]
            if len(sub) < 3: continue
            vals = ''.join([f"  {sub[f'{p}_match'].mean()*100:>9.1f}%" for p in PAIRS])
            base = act[f'{pair}_match'].mean()*100
            mr   = sub[f'{pair}_match'].mean()*100
            lift = mr - base
            print(f"  {lbl:<40}  N={len(sub):>3}{vals}   [{pair} lift: {lift:+.1f}pp]")

    print()

    # ??? SECTION 5: TOP COMBINED FILTERS PER PAIR ?????????????????????????????
    print("=" * 76)
    print("  5. TOP COMBINED FILTERS  (DXY_wpr + one more, N?5)")
    print("=" * 76)
    priority_cols = ['DXY_wpr_os', 'DXY_wpr_ob']

    for pair in PAIRS:
        base = act[f'{pair}_match'].mean()*100
        bool_cols = [c for c in act.columns
                     if c.endswith(('_pos','_os','_ob','_trend','_bull','_bear'))
                     and (c.startswith('DXY_') or c.startswith(f'{pair}_'))]
        combos = []
        for prim in priority_cols:
            if prim not in act.columns: continue
            for sec in bool_cols:
                if sec == prim: continue
                sub = act[(act[prim]==True) & (act[sec]==True)]
                if len(sub) < 5: continue
                mr = sub[f'{pair}_match'].mean()*100
                combos.append({'filter': f"{prim} + {sec}", 'N': len(sub),
                               'Match%': round(mr,1), 'Lift': round(mr-base,1)})
        if combos:
            cdf = pd.DataFrame(combos).sort_values('Lift', ascending=False).head(8)
            print(f"\n  {pair} {corr_labels[pair]}  ?  baseline {base:.1f}%")
            print(f"  {'Filter':<50}  {'N':>5}  {'Match%':>7}  {'Lift':>7}")
            print(f"  {'-'*72}")
            for _, r in cdf.iterrows():
                print(f"  {r['filter']:<50}  {r['N']:>5}  {r['Match%']:>6.1f}%  {r['Lift']:>+6.1f}pp")
    print()

    # ??? SECTION 6: CONTINUOUS INDICATOR MEAN ? MATCH vs NO-MATCH ?????????????
    print("=" * 76)
    print("  6. INDICATOR MEAN COMPARISON  (match vs no-match days)")
    print("=" * 76)
    cont_base  = ['DXY_wpr','DXY_rsi','DXY_adx_4h','DXY_macd_h']
    for pair in PAIRS:
        matched   = act[act[f'{pair}_match']==True]
        unmatched = act[act[f'{pair}_match']==False]
        cols = cont_base + [f'{pair}_wpr', f'{pair}_rsi', f'{pair}_adx_4h', f'{pair}_macd_h']
        print(f"\n  {pair} {corr_labels[pair]}  matched={len(matched)}  not-matched={len(unmatched)}")
        print(f"  {'Indicator':<22}  {'Match mean':>11}  {'NoMatch mean':>13}  {'Diff':>8}  {'Direction':>12}")
        print(f"  {'-'*72}")
        for col in cols:
            if col not in act.columns: continue
            mm = matched[col].mean()   if not matched.empty   else np.nan
            nm = unmatched[col].mean() if not unmatched.empty else np.nan
            d  = mm - nm
            lbl = col.replace(f'{pair}_','').replace('DXY_','DXY:')
            # direction note for key indicators
            note = ''
            if 'wpr' in col:
                note = 'MATCH more oversold' if d < -3 else ('MATCH more overbought' if d > 3 else 'similar')
            elif 'adx' in col:
                note = 'MATCH stronger trend' if d > 1 else ('MATCH weaker trend' if d < -1 else 'similar')
            print(f"  {lbl:<22}  {mm:>11.3f}  {nm:>13.3f}  {d:>+8.3f}  {note:>12}")

    print()
    print(f"  Full log saved: {BASE / 'dxy_london_patterns_v2.csv'}")
    print()

if __name__ == '__main__':
    main()
