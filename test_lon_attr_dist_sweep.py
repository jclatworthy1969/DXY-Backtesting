"""
test_lon_attr_dist_sweep.py
===========================
Sweep minimum distance thresholds for LON_ATTR — LONG and SHORT independently.

Strategy:
  LONG  TP = zone_bot (near edge) — best per zone TP analysis
  SHORT TP = zone_bot (far  edge) — best per zone TP analysis
  SL = 1:1 mirror of TP distance, zone-based pristine, pin bar only.

Method:
  Single scan with no lon_attr_traded restriction — collects every qualifying
  signal per session per direction. For each threshold the simulation then picks
  the first signal in each session that satisfies dist >= threshold, matching the
  original "one trade per session" behaviour. LONG and SHORT are swept independently
  so their optimal thresholds can be found without coupling.

Thresholds tested: 100 – 2500 pts in 100-pt steps.
"""
import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
import dxy_improved_rules as imp
import dxy_clean_rules as r

ENTRY_END  = 18 * 60
THRESHOLDS = list(range(100, 2600, 100))
MIN_N      = 10          # minimum sample size to consider a result meaningful

# ── Load ────────────────────────────────────────────────────────────────────
df_dxy = imp.load_merged('DXY')
df_dxy = df_dxy.copy().reset_index(drop=True)
news_dates = r.load_news_filter()
months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
print(f"DXY: {len(df_dxy):,} bars  |  {months:.1f} months\n")

# ── Pin bar series ──────────────────────────────────────────────────────────
c_s = df_dxy['close']; o_s = df_dxy['open']
h_s = df_dxy['high'];  l_s = df_dxy['low']
body        = (c_s - o_s).abs()
body_top    = pd.concat([o_s, c_s], axis=1).max(axis=1)
body_bottom = pd.concat([o_s, c_s], axis=1).min(axis=1)
hi_wick     = h_s - body_top
lo_wick     = body_bottom - l_s
rng_s       = (h_s - l_s).replace(0, np.nan)
PMW         = r.PIN_WICK_MULT

bull_pin = (lo_wick >= body * PMW) & (lo_wick >= hi_wick * 1.5) & rng_s.notna()
bear_pin = (hi_wick >= body * PMW) & (hi_wick >= lo_wick * 1.5) & rng_s.notna()
both     = bull_pin & bear_pin
bull_pin = bull_pin & ~(both & (c_s <= o_s))
bear_pin = bear_pin & ~(both & (c_s >= o_s))


# ── Single full scan — no lon_attr_traded restriction ───────────────────────
# Collect every qualifying signal (pristine + pin) with its outcome so the
# threshold sweep can filter without re-scanning.
print("Running base scan (single pass, collecting all signals)...")

london_open_price  = np.nan
zone_top           = np.nan
zone_bot           = np.nan
lon_pristine_long  = True
lon_pristine_short = True
session_id         = 0
all_sigs = []

for i in range(2, len(df_dxy)):
    row = df_dxy.iloc[i]
    cv, ov = row['close'], row['open']
    ts = row['time']
    hour, minute = ts.hour, ts.minute
    curr_min = hour * 60 + minute
    dow = ts.dayofweek
    in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

    is_lon = (not in_japan and hour == 7 and minute == 0 and dow != 0)
    is_mon = (not in_japan and hour == 6 and minute == 30 and dow == 0)

    if is_lon or is_mon:
        london_open_price  = ov
        zone_top           = max(ov, cv)
        zone_bot           = min(ov, cv)
        lon_pristine_long  = True
        lon_pristine_short = True
        session_id        += 1
        continue

    if np.isnan(london_open_price) or in_japan:
        continue

    # Zone violation tracking
    if not np.isnan(zone_top):
        if ov >= zone_top or cv >= zone_top:
            lon_pristine_long  = False
        if ov <= zone_bot or cv <= zone_bot:
            lon_pristine_short = False

    lon_start = (6*60+30) if dow == 0 else (7*60)
    if not (lon_start < curr_min <= ENTRY_END):
        continue

    ts_str = str(ts)
    if news_dates and r.news_blocks_pair(news_dates, ts_str, 'ALL_USD'):
        continue

    dist_pts = (cv - london_open_price) * 10000  # negative = below open

    # ── LONG candidate ─────────────────────────────────────────────────
    if lon_pristine_long and bull_pin.at[i] and dist_pts < 0:
        long_dist = round(-dist_pts)   # positive value = pts below open
        tp = zone_bot
        if tp > cv:                    # TP must be above entry for LONG
            sl = cv - (tp - cv)
            out, _, _ = r.resolve(df_dxy, i, cv, tp, sl, 'long')
        else:
            out = 'na'
        all_sigs.append({
            'dir': 'LONG', 'session': session_id, 'bar': i,
            'dist': long_dist, 'out': out,
        })

    # ── SHORT candidate ────────────────────────────────────────────────
    if lon_pristine_short and bear_pin.at[i] and dist_pts > 0:
        short_dist = round(dist_pts)   # positive value = pts above open
        tp = zone_bot                  # far edge of zone for SHORT
        if tp < cv:                    # TP must be below entry for SHORT
            sl = cv + (cv - tp)
            out, _, _ = r.resolve(df_dxy, i, cv, tp, sl, 'short')
        else:
            out = 'na'
        all_sigs.append({
            'dir': 'SHORT', 'session': session_id, 'bar': i,
            'dist': short_dist, 'out': out,
        })

df_all = pd.DataFrame(all_sigs)
# Sort by session then bar so "first qualifying" logic works correctly
df_all = df_all.sort_values(['session', 'bar']).reset_index(drop=True)

print(f"Base scan complete: {len(df_all[df_all['dir']=='LONG'])} LONG candidates, "
      f"{len(df_all[df_all['dir']=='SHORT'])} SHORT candidates across "
      f"{df_all['session'].nunique()} sessions\n")


# ── Threshold simulation ────────────────────────────────────────────────────
def simulate_threshold(direction, min_dist):
    """
    For the given direction, select the first qualifying signal in each session
    where dist >= min_dist. Returns the winning/losing/na counts.
    """
    sub = df_all[(df_all['dir'] == direction) & (df_all['dist'] >= min_dist)]
    # First qualifying signal per session
    first = sub.groupby('session').first().reset_index()
    valid = first[first['out'] != 'na']
    n  = len(valid)
    w  = (valid['out'] == 'win').sum()
    l  = (valid['out'] == 'loss').sum()
    wr = w / (w + l) * 100 if (w + l) > 0 else float('nan')
    return len(first), n, int(w), int(l), wr, int(w) - int(l)


# ── Results tables ──────────────────────────────────────────────────────────
long_rows  = []
short_rows = []

for t in THRESHOLDS:
    tot_l, n_l, w_l, l_l, wr_l, net_l = simulate_threshold('LONG',  t)
    tot_s, n_s, w_s, l_s, wr_s, net_s = simulate_threshold('SHORT', t)
    long_rows.append( {'thresh': t, 'total': tot_l, 'n': n_l, 'w': w_l, 'l': l_l, 'wr': wr_l, 'net': net_l})
    short_rows.append({'thresh': t, 'total': tot_s, 'n': n_s, 'w': w_s, 'l': l_s, 'wr': wr_s, 'net': net_s})

df_lr = pd.DataFrame(long_rows)
df_sr = pd.DataFrame(short_rows)


def print_table(df, title):
    print("=" * 76)
    print(f"  {title}")
    print("=" * 76)
    print(f"  {'MinDist':>8}  {'Trades':>7}  {'Valid':>6}  {'W':>4}  {'L':>4}  {'WR%':>7}  {'Net':>7}")
    print(f"  {'-'*64}")
    for _, row in df.iterrows():
        wr_s  = f"{row['wr']:.1f}%" if not np.isnan(row['wr']) else "    n/a"
        flag  = " <--" if row['n'] >= MIN_N and not np.isnan(row['wr']) and row['wr'] >= 55 else ""
        print(f"  {row['thresh']:>8}  {row['total']:>7}  {row['n']:>6}  "
              f"{row['w']:>4}  {row['l']:>4}  {wr_s:>7}  {row['net']:>+7}R{flag}")
    # Best WR with enough data
    valid = df[df['n'] >= MIN_N].copy()
    if not valid.empty and not valid['wr'].isna().all():
        best = valid.loc[valid['wr'].idxmax()]
        print(f"\n  >> Best WR (N>={MIN_N}): {best['thresh']:.0f} pts  ->  "
              f"{best['wr']:.1f}% WR  |  N={best['n']:.0f}  |  Net {best['net']:+.0f}R")
        # Also show best net R (positive net with decent WR)
        pos_net = valid[valid['net'] > 0]
        if not pos_net.empty:
            best_net = pos_net.loc[pos_net['net'].idxmax()]
            if best_net['thresh'] != best['thresh']:
                print(f"  >> Best Net R (N>={MIN_N}): {best_net['thresh']:.0f} pts  ->  "
                      f"{best_net['wr']:.1f}% WR  |  N={best_net['n']:.0f}  |  Net {best_net['net']:+.0f}R")
    print()


print_table(df_lr, "LONG  — TP = zone_bot (near edge), SL = 1:1, zone pristine, pin bar")
print_table(df_sr, "SHORT — TP = zone_bot (far  edge), SL = 1:1, zone pristine, pin bar")


# ── Condensed view: only thresholds near the user's hypothesis ───────────────
print("=" * 76)
print("  FOCUS: 700 – 1800 pts range (LONG) and 600 – 1400 pts range (SHORT)")
print("=" * 76)
print()
print("  LONG:")
for _, row in df_lr[(df_lr['thresh'] >= 700) & (df_lr['thresh'] <= 1800)].iterrows():
    wr_s = f"{row['wr']:.1f}%" if not np.isnan(row['wr']) else "n/a"
    bar  = "#" * max(0, int(row['wr'] or 0) - 40)
    print(f"    {row['thresh']:>5} pts: {wr_s:>6}  N={row['n']:>3}  Net {row['net']:>+4}R  {bar}")

print()
print("  SHORT:")
for _, row in df_sr[(df_sr['thresh'] >= 600) & (df_sr['thresh'] <= 1400)].iterrows():
    wr_s = f"{row['wr']:.1f}%" if not np.isnan(row['wr']) else "n/a"
    bar  = "#" * max(0, int(row['wr'] or 0) - 40)
    print(f"    {row['thresh']:>5} pts: {wr_s:>6}  N={row['n']:>3}  Net {row['net']:>+4}R  {bar}")

print()
