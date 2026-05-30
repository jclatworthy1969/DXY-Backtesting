"""
test_lon_attr_zone_tp.py
========================
LON_ATTR zone analysis: compare TP at three levels —
  NEAR  : near edge of first session candle body (zone_bot for LONG, zone_top for SHORT)
  OPEN  : London open price (current setting)
  FAR   : far edge of first session candle body (zone_top for LONG, zone_bot for SHORT)

Uses zone-based pristine definition matching the updated Pine scripts:
  - zone_top = max(open, close) of London open bar
  - zone_bot = min(open, close) of London open bar
  - pristine_long  : no subsequent candle open/close >= zone_top
  - pristine_short : no subsequent candle open/close <= zone_bot

Pin bars only. No divergence filter. Min dist = 100 pts. SL = 1:1 per TP.
"""
import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
import dxy_improved_rules as imp
import dxy_clean_rules as r

MIN_DIST    = 100
ENTRY_END   = 18 * 60

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


# ── Scanner ─────────────────────────────────────────────────────────────────
def scan(min_dist=MIN_DIST):
    london_open_price  = np.nan
    zone_top           = np.nan
    zone_bot           = np.nan
    lon_pristine_long  = True
    lon_pristine_short = True
    lon_attr_traded    = False
    sigs = []

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
            lon_attr_traded    = False
            continue

        if np.isnan(london_open_price) or in_japan:
            continue

        # Zone violation tracking (all non-Japan, non-open bars)
        if not np.isnan(zone_top):
            if ov >= zone_top or cv >= zone_top:
                lon_pristine_long  = False
            if ov <= zone_bot or cv <= zone_bot:
                lon_pristine_short = False

        lon_start = (6*60+30) if dow == 0 else (7*60)
        if not (lon_start < curr_min <= ENTRY_END):
            continue
        if lon_attr_traded:
            continue
        ts_str = str(ts)
        if news_dates and r.news_blocks_pair(news_dates, ts_str, 'ALL_USD'):
            continue

        dist = (cv - london_open_price) * 10000

        # ── LONG ───────────────────────────────────────────────────────────
        if dist <= -min_dist and lon_pristine_long and bull_pin.at[i]:
            # TP levels (all should be above entry for a LONG)
            tp_near = zone_bot           # closest edge of zone body
            tp_open = london_open_price  # open of first session bar
            tp_far  = zone_top           # furthest edge of zone body

            def res_long(tp):
                if tp <= cv:             # TP below entry → invalid
                    return 'na', np.nan
                sl = cv - (tp - cv)      # 1:1 mirror
                out, _, _ = r.resolve(df_dxy, i, cv, tp, sl, 'long')
                return out, round((tp - cv) * 10000)

            out_near, pts_near = res_long(tp_near)
            out_open, pts_open = res_long(tp_open)
            out_far,  pts_far  = res_long(tp_far)

            sigs.append({
                'dir': 'LONG', 'date': ts_str[:10],
                'dist': round(-dist),
                'zone_sz': round((zone_top - zone_bot) * 10000),
                'pts_near': pts_near, 'pts_open': pts_open, 'pts_far': pts_far,
                'out_near': out_near, 'out_open': out_open, 'out_far': out_far,
            })
            lon_attr_traded = True

        # ── SHORT ──────────────────────────────────────────────────────────
        elif dist >= min_dist and lon_pristine_short and bear_pin.at[i]:
            tp_near = zone_top
            tp_open = london_open_price
            tp_far  = zone_bot

            def res_short(tp):
                if tp >= cv:             # TP above entry → invalid
                    return 'na', np.nan
                sl = cv + (cv - tp)      # 1:1 mirror
                out, _, _ = r.resolve(df_dxy, i, cv, tp, sl, 'short')
                return out, round((cv - tp) * 10000)

            out_near, pts_near = res_short(tp_near)
            out_open, pts_open = res_short(tp_open)
            out_far,  pts_far  = res_short(tp_far)

            sigs.append({
                'dir': 'SHORT', 'date': ts_str[:10],
                'dist': round(dist),
                'zone_sz': round((zone_top - zone_bot) * 10000),
                'pts_near': pts_near, 'pts_open': pts_open, 'pts_far': pts_far,
                'out_near': out_near, 'out_open': out_open, 'out_far': out_far,
            })
            lon_attr_traded = True

    return pd.DataFrame(sigs)


# ── Stats helper ────────────────────────────────────────────────────────────
def wr_stats(sub, col):
    valid = sub[sub[col] != 'na']
    n = len(valid)
    if n == 0:
        return 0, 0, 0, float('nan'), float('nan')
    w = (valid[col] == 'win').sum()
    l = (valid[col] == 'loss').sum()
    wr = w / (w + l) * 100 if (w + l) > 0 else float('nan')
    net = int(w) - int(l)
    avg_pts = valid[col.replace('out_', 'pts_')].mean() if col.replace('out_', 'pts_') in valid.columns else float('nan')
    return n, w, l, wr, net


# ── Run ─────────────────────────────────────────────────────────────────────
df = scan()
print(f"Signals found (dist >= {MIN_DIST} pts, zone pristine): {len(df)}")

zone_sizes = df['zone_sz']
print(f"\nZone size (first session candle body, pts):")
print(f"  mean={zone_sizes.mean():.0f}  median={zone_sizes.median():.0f}  "
      f"min={zone_sizes.min():.0f}  max={zone_sizes.max():.0f}")
print(f"  Doji (zone=0): {(zone_sizes == 0).sum()}  "
      f"Zone < 50pts: {(zone_sizes < 50).sum()}  "
      f"Zone >= 100pts: {(zone_sizes >= 100).sum()}")

# ── Main comparison table ────────────────────────────────────────────────────
print()
print("=" * 80)
print("  TP COMPARISON  (pin bar, dist >= 100 pts, zone pristine, SL = 1:1 per TP)")
print("=" * 80)
print(f"  {'Subset':<16} {'TP Level':<10} {'N':>5} {'W':>4} {'L':>4} "
      f"{'WR%':>6} {'Net':>6} {'Avg TP pts':>10}")
print(f"  {'-'*70}")

for subset_name, subset in [('ALL', df), ('LONG', df[df['dir']=='LONG']), ('SHORT', df[df['dir']=='SHORT'])]:
    for tp_label, col in [('NEAR edge', 'out_near'), ('OPEN price', 'out_open'), ('FAR  edge', 'out_far')]:
        n, w, l, wr, net = wr_stats(subset, col)
        pts_col = col.replace('out_', 'pts_')
        avg_pts = subset[subset[col] != 'na'][pts_col].mean() if n > 0 else float('nan')
        wr_s = f"{wr:.1f}%" if not np.isnan(wr) else "  n/a"
        avg_s = f"{avg_pts:.0f}" if not np.isnan(avg_pts) else "n/a"
        print(f"  {subset_name:<16} {tp_label:<10} {n:>5} {w:>4} {l:>4} {wr_s:>6} {net:>+6}R {avg_s:>9}pts")
    print()

# ── Coverage: how often is near TP valid? ───────────────────────────────────
print("=" * 80)
print("  NEAR TP VALIDITY  (near TP > entry — i.e. zone has depth above entry)")
print("=" * 80)
for subset_name, subset in [('LONG', df[df['dir']=='LONG']), ('SHORT', df[df['dir']=='SHORT'])]:
    valid_near = (subset['out_near'] != 'na').sum()
    total = len(subset)
    print(f"  {subset_name}: near TP valid on {valid_near}/{total} trades "
          f"({valid_near/total*100:.0f}%) — "
          f"zone size zero/doji: {(subset['zone_sz']==0).sum()} trades")
print()

# ── Detail table ─────────────────────────────────────────────────────────────
print("=" * 80)
print("  PER-TRADE DETAIL")
print("=" * 80)
print(f"  {'Date':<12} {'Dir':<6} {'Dist':>5} {'ZoneSz':>7} "
      f"{'Near':>6} {'NrOut':<6} {'Open':>6} {'OpOut':<6} {'Far':>6} {'FarOut':<6}")
print(f"  {'-'*78}")
for _, row in df.sort_values('date').iterrows():
    def fmt(pts, out):
        if out == 'na':
            return f"{'---':>6}", f"{'na':<6}"
        mk = 'W' if out == 'win' else ('L' if out == 'loss' else '~')
        return f"{pts:>6.0f}", f"{mk+' '+out[:3]:<6}"
    nr_p, nr_o = fmt(row['pts_near'], row['out_near'])
    op_p, op_o = fmt(row['pts_open'], row['out_open'])
    fa_p, fa_o = fmt(row['pts_far'],  row['out_far'])
    print(f"  {row['date']:<12} {row['dir']:<6} {row['dist']:>5.0f} {row['zone_sz']:>7.0f} "
          f"{nr_p} {nr_o} {op_p} {op_o} {fa_p} {fa_o}")

print()
