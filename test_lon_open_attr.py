"""
test_lon_open_attr.py
======================
Hypothesis: Attraction trade TOWARD the London Open level.

Conditions (all required):
  1. Price is >= MIN_DIST_PTS from the London open price
  2. London open level is PRISTINE — price has moved in one direction only
     and has NOT yet crossed back to the other side of the London open price
  3. Clear entry candle (engulf / pin / 3-bar) pointing TOWARD London open
  4. Divergence score >= MIN_DIV_SCORE on 6 oscillators

TP:  London open price (the level being attracted to)
SL:  structural stop (prior session extreme + 50 pt buffer)

Resolution is on DXY bars — tests whether DXY itself fills back to its
own London open level.  Sweeps MIN_DIST_PTS and MIN_DIV_SCORE.
"""

import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
import dxy_improved_rules as imp
import dxy_clean_rules as r

# ── Parameters ──────────────────────────────────────────────────────────────────
BASE_MIN_DIST  = 300   # pts from London open at entry
BASE_MIN_DIV   = 1     # minimum oscillators showing divergence (out of 6)
DIV_LOOKBACK   = 30    # rolling extreme lookback (bars)
ENTRY_WIN_END  = 18 * 60   # 18:00 UTC — allow whole London/NY session

# ── Oscillator functions ────────────────────────────────────────────────────────
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _rsi(c, p=14):
    d=c.diff(); g=_ema(d.clip(lower=0),p); l=_ema((-d).clip(lower=0),p)
    return 100-100/(1+g/l.replace(0,np.nan))
def _macd_h(c,f=12,s=26,sig=9):
    ml=_ema(c,f)-_ema(c,s); return ml-_ema(ml,sig)
def _stoch(c,h,l,p=14):
    lo=l.rolling(p).min(); hi=h.rolling(p).max(); rng=hi-lo
    return pd.Series(np.where(rng>0,(c-lo)/rng*100,50),index=c.index)
def _cci(hlc3,p=20):
    sma=hlc3.rolling(p).mean()
    mad=hlc3.rolling(p).apply(lambda x:np.mean(np.abs(x-x.mean())),raw=True)
    return pd.Series(np.where(mad>0,(hlc3-sma)/(0.015*mad),0),index=hlc3.index)
def _wpr(c,h,l,p=14):
    hi=h.rolling(p).max(); lo=l.rolling(p).min(); rng=hi-lo
    return pd.Series(np.where(rng>0,(hi-c)/rng*-100,-50),index=c.index)
def _mom(c,p=10): return c-c.shift(p)

OSCILS = ['rsi','macd_h','stoch_k','cci_v','wpr_v','mom_v']
LB = DIV_LOOKBACK

def add_oscils(df):
    df=df.copy(); hlc3=(df['high']+df['low']+df['close'])/3
    df['rsi']=_rsi(df['close']); df['macd_h']=_macd_h(df['close'])
    df['stoch_k']=_stoch(df['close'],df['high'],df['low'])
    df['cci_v']=_cci(hlc3); df['wpr_v']=_wpr(df['close'],df['high'],df['low'])
    df['mom_v']=_mom(df['close']); return df

def bull_div_sc(df):
    lo=df['close'].rolling(LB,min_periods=LB//2).min()
    hi=df['close'].rolling(LB,min_periods=LB//2).max()
    rng=hi-lo; at_low=(rng>0)&(df['close']<=lo+rng*0.30)
    sc=pd.Series(0.0,index=df.index)
    for col in OSCILS:
        vlo=df[col].rolling(LB,min_periods=LB//2).min()
        vhi=df[col].rolling(LB,min_periods=LB//2).max()
        vrng=vhi-vlo
        sc+=((at_low)&(vrng>0)&(df[col]>vlo+vrng*0.30)).astype(float)
    return sc

def bear_div_sc(df):
    lo=df['close'].rolling(LB,min_periods=LB//2).min()
    hi=df['close'].rolling(LB,min_periods=LB//2).max()
    rng=hi-lo; at_hi=(rng>0)&(df['close']>=hi-rng*0.30)
    sc=pd.Series(0.0,index=df.index)
    for col in OSCILS:
        vlo=df[col].rolling(LB,min_periods=LB//2).min()
        vhi=df[col].rolling(LB,min_periods=LB//2).max()
        vrng=vhi-vlo
        sc+=((at_hi)&(vrng>0)&(df[col]<vhi-vrng*0.30)).astype(float)
    return sc


# ── Load & prepare ──────────────────────────────────────────────────────────────
print("Loading data...")
df_dxy = imp.load_merged('DXY')
months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
print(f"  DXY: {len(df_dxy):,} bars  "
      f"({df_dxy['time'].min().date()} to {df_dxy['time'].max().date()}, "
      f"{months:.1f} months)")

print("Computing oscillators and divergence scores...")
df_ind = add_oscils(df_dxy)
df_ind['bull_div'] = bull_div_sc(df_ind)
df_ind['bear_div'] = bear_div_sc(df_ind)
bull_map = dict(zip(df_ind['time'].astype(str), df_ind['bull_div']))
bear_map = dict(zip(df_ind['time'].astype(str), df_ind['bear_div']))

print("Computing candle signals...")
bull_sig, bear_sig = imp.candle_signals_v2(df_dxy)

# Daily high/low for structural SL
df_dxy = df_dxy.copy().reset_index(drop=True)
df_dxy['_date'] = df_dxy['time'].dt.date
day_grp = df_dxy.groupby('_date').agg(day_h=('high','max'), day_l=('low','min'))
news_dates = r.load_news_filter()


# ── Core scanner ────────────────────────────────────────────────────────────────
def scan_lon_attr(min_dist, min_div):
    """
    Scan DXY bars for LON_ATTR signals.

    Pristine definition
    -------------------
    For a LONG (price below London open, TP = London open):
      Price must never have been ABOVE the London open during this session.
      i.e., the London open level has not yet been re-crossed upward.

    For a SHORT (price above London open, TP = London open):
      Price must never have been BELOW the London open during this session.
      i.e., the London open level has not yet been re-crossed downward.

    One signal per session (lon_traded flag).
    """
    london_open_price = np.nan
    prev_session_high = np.nan
    prev_session_low  = np.nan
    seen_above_open   = False   # price closed above London open this session
    seen_below_open   = False   # price closed below London open this session
    lon_traded        = False   # one signal per session

    sigs = []

    for i in range(2, len(df_dxy)):
        row = df_dxy.iloc[i]
        c, o = row['close'], row['open']
        ts     = row['time']
        hour   = ts.hour
        minute = ts.minute
        curr_min = hour * 60 + minute
        dow = ts.dayofweek

        in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

        is_london_open = (not in_japan and hour == 7 and minute == 0 and dow != 0)
        is_monday_open = (not in_japan and hour == 6 and minute == 30 and dow == 0)

        # Reset at London/Monday open
        if is_london_open or is_monday_open:
            london_open_price = o
            seen_above_open   = False
            seen_below_open   = False
            lon_traded        = False
            today_dt   = ts.date()
            prior_days = [d for d in day_grp.index if d < today_dt]
            if prior_days:
                prev_dt           = max(prior_days)
                prev_session_high = float(day_grp.at[prev_dt, 'day_h'])
                prev_session_low  = float(day_grp.at[prev_dt, 'day_l'])
            else:
                prev_session_high = np.nan
                prev_session_low  = np.nan
            continue

        if np.isnan(london_open_price) or in_japan:
            continue

        # Entry window: strictly after London open until 18:00
        lon_start = (6 * 60 + 30) if dow == 0 else (7 * 60)
        if not (lon_start < curr_min <= ENTRY_WIN_END):
            continue

        dist = (c - london_open_price) * 10000

        # Update pristine tracking BEFORE checking signals
        if c > london_open_price:
            seen_above_open = True
        elif c < london_open_price:
            seen_below_open = True

        if lon_traded or np.isnan(prev_session_high):
            continue

        # News filter (same as ATTR/GAP_REJ)
        ts_str = str(ts)
        if news_dates and r.news_blocks_pair(news_dates, ts_str, 'ALL_USD'):
            continue

        b_sc  = bull_map.get(ts_str, 0)
        br_sc = bear_map.get(ts_str, 0)

        # ── LONG: price >= min_dist BELOW London open, never crossed above, bull signal
        if (dist <= -min_dist
                and not seen_above_open
                and bull_sig.at[i]
                and b_sc >= min_div):

            tp_p = london_open_price
            sl_p = imp.get_structural_sl(prev_session_low, prev_session_high, c, 'long')
            sl_d = c - sl_p
            tp_d = (tp_p - c) * 10000
            if sl_d > 0:
                rr = tp_d / (sl_d * 10000)
                outcome, exit_px, exit_bar = r.resolve(df_dxy, i, c, tp_p, sl_p, 'long')
                sigs.append({
                    'type': 'LON_ATTR_LONG', 'entry_time': ts_str,
                    'entry': round(c, 5), 'tp': round(tp_p, 5), 'sl': round(sl_p, 5),
                    'sl_pts': round(sl_d * 10000), 'tp_pts': round(tp_d),
                    'dist_pts': round(-dist), 'div': b_sc, 'rr': round(rr, 2),
                    'london_open': round(london_open_price, 5),
                    'outcome': outcome, 'exit_px': round(exit_px, 5),
                    'exit_time': str(df_dxy.at[exit_bar, 'time']),
                })
                lon_traded = True

        # ── SHORT: price >= min_dist ABOVE London open, never crossed below, bear signal
        elif (dist >= min_dist
                  and not seen_below_open
                  and bear_sig.at[i]
                  and br_sc >= min_div):

            tp_p = london_open_price
            sl_p = imp.get_structural_sl(prev_session_low, prev_session_high, c, 'short')
            sl_d = sl_p - c
            tp_d = (c - tp_p) * 10000
            if sl_d > 0:
                rr = tp_d / (sl_d * 10000)
                outcome, exit_px, exit_bar = r.resolve(df_dxy, i, c, tp_p, sl_p, 'short')
                sigs.append({
                    'type': 'LON_ATTR_SHORT', 'entry_time': ts_str,
                    'entry': round(c, 5), 'tp': round(tp_p, 5), 'sl': round(sl_p, 5),
                    'sl_pts': round(sl_d * 10000), 'tp_pts': round(tp_d),
                    'dist_pts': round(dist), 'div': br_sc, 'rr': round(rr, 2),
                    'london_open': round(london_open_price, 5),
                    'outcome': outcome, 'exit_px': round(exit_px, 5),
                    'exit_time': str(df_dxy.at[exit_bar, 'time']),
                })
            lon_traded = True

    return sigs


# ── Stats helper ────────────────────────────────────────────────────────────────
def stats(sigs, label, width=50):
    if not sigs:
        return f"  {label:<{width}}: No signals"
    n = len(sigs)
    w = sum(1 for s in sigs if s['outcome'] == 'win')
    l = sum(1 for s in sigs if s['outcome'] == 'loss')
    t = sum(1 for s in sigs if s['outcome'] == 'timeout')
    wr = w / (w + l) * 100 if (w + l) > 0 else 0
    pf = w / l if l > 0 else float('inf')
    net = w - l
    pf_s = f"{pf:.3f}" if pf != float('inf') else "inf"
    t_s  = f" T:{t}" if t > 0 else ""
    return (f"  {label:<{width}}: N={n:3d}  W={w:3d} L={l:3d}{t_s:<5}"
            f"  WR={wr:5.1f}%  PF={pf_s:<6}  Net={net:+d}R")


# ── Baseline scan: MIN_DIST=300, MIN_DIV=1 ─────────────────────────────────────
print()
print("Scanning baseline (300 pts, div >= 1)...")
base_sigs = scan_lon_attr(BASE_MIN_DIST, BASE_MIN_DIV)
print(f"  Found {len(base_sigs)} LON_ATTR signals")


# ── Per-signal detail ───────────────────────────────────────────────────────────
if base_sigs:
    print()
    print("=" * 90)
    print(f"  LON_ATTR SIGNALS — baseline (>= {BASE_MIN_DIST} pts from London open, div >= {BASE_MIN_DIV})")
    print("=" * 90)
    print(f"  {'Date':<12} {'Type':<16} {'Dist':>5} {'TPpts':>6} {'SLpts':>6} {'R:R':>5} {'Div':>4}  Outcome")
    print(f"  {'-'*72}")
    for s in sorted(base_sigs, key=lambda x: x['entry_time']):
        out_c = 'W' if s['outcome'] == 'win' else ('L' if s['outcome'] == 'loss' else '~')
        print(f"  {s['entry_time'][:10]:<12} {s['type']:<16} "
              f"{s['dist_pts']:>5.0f} {s['tp_pts']:>6.0f} {s['sl_pts']:>6.0f} "
              f"{s['rr']:>5.2f} {s['div']:>4.0f}  "
              f"{out_c} {s['outcome']}")


# ── Divergence score distribution ──────────────────────────────────────────────
all_sigs_nodiv = scan_lon_attr(BASE_MIN_DIST, 0)
if all_sigs_nodiv:
    print()
    print("=" * 90)
    print(f"  DIVERGENCE SCORE DISTRIBUTION — all LON_ATTR signals >= {BASE_MIN_DIST} pts (no div filter)")
    print("=" * 90)
    score_counts = pd.Series([s['div'] for s in all_sigs_nodiv]).value_counts().sort_index()
    for score, cnt in score_counts.items():
        subset = [s for s in all_sigs_nodiv if s['div'] == score]
        w = sum(1 for s in subset if s['outcome'] == 'win')
        l = sum(1 for s in subset if s['outcome'] == 'loss')
        wr = w / (w + l) * 100 if (w + l) > 0 else 0
        net = w - l
        print(f"  Score {int(score)}/6 : {cnt:3d} signals  W={w:2d} L={l:2d}  WR={wr:5.1f}%  Net={net:+d}R")


# ── Distance sweep ──────────────────────────────────────────────────────────────
print()
print("=" * 90)
print("  DISTANCE SWEEP — varying minimum distance from London open (div >= 1)")
print("=" * 90)
for d_thresh in [100, 150, 200, 300, 400, 500]:
    sigs = scan_lon_attr(d_thresh, 1)
    print(stats(sigs, f"dist >= {d_thresh} pts,  div >= 1"))

# ── Divergence sweep ────────────────────────────────────────────────────────────
print()
print("=" * 90)
print("  DIVERGENCE SWEEP — varying min div score (dist >= 300 pts)")
print("=" * 90)
for div_thresh in [0, 1, 2, 3]:
    sigs = scan_lon_attr(300, div_thresh)
    label = f"dist >= 300 pts,  div >= {div_thresh}"
    print(stats(sigs, label))

# ── Combined sweep ──────────────────────────────────────────────────────────────
print()
print("=" * 90)
print("  COMBINED SWEEP — distance x divergence")
print("=" * 90)
for d_thresh in [200, 300, 400]:
    for div_thresh in [0, 1, 2]:
        sigs = scan_lon_attr(d_thresh, div_thresh)
        label = f"dist >= {d_thresh},  div >= {div_thresh}"
        print(stats(sigs, label))
    print()

# ── R:R-filtered results (only take trades with TP > SL, i.e., R:R >= 1) ───────
print()
print("=" * 90)
print("  R:R FILTER — same signals but only where TP distance >= SL distance (R:R >= 1)")
print("=" * 90)
for d_thresh in [200, 300, 400]:
    for div_thresh in [0, 1, 2]:
        all_s = scan_lon_attr(d_thresh, div_thresh)
        good_rr = [s for s in all_s if s['rr'] >= 1.0]
        label = f"dist >= {d_thresh},  div >= {div_thresh},  R:R >= 1"
        print(stats(good_rr, label))
    print()

print()
