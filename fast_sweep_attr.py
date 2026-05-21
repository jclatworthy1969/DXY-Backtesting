"""
fast_sweep_attr.py
==================
ATTR parameter sweep — fast version.

Strategy:
  Run generate_dxy_signals TWICE (near_tp=True / False) with the LOOSEST
  thresholds (gap=50, approach=50, reward=50).  Each signal is tagged with
  the actual gap_pts / approach_pts / reward_pts recorded at fire time.

  For each of the 686 (gap × approach × reward × tp_mode) combos we just
  filter the pre-generated universe — no re-running the 63k-bar loop.
  DXY-exit is then applied to the filtered subset.

  ~2 slow runs + 686 fast filter passes → typically 2-5 minutes total.

NOTE ON APPROXIMATION:
  Because of the in_trade_until blocking in the original loop, pre-generating
  with loose params can block a slot that tight params would leave open. This
  slightly under-counts signals for tight combos. Relative rankings are
  preserved; absolute NetR may differ by a few percent from an exact run.
  The best combo(s) found here should be verified with a single exact run.
"""

import pandas as pd
import numpy as np
import itertools
from pathlib import Path

import dxy_clean_rules as r

BASE  = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
PAIRS = r.PAIRS

# ── Sweep grid ────────────────────────────────────────────────────────────────
GAP_VALUES      = [50, 75, 100, 125, 150, 175, 200]
APPROACH_VALUES = [50, 75, 100, 125, 150, 175, 200]
REWARD_VALUES   = [50, 75, 100, 125, 150, 175, 200]
TP_MODES        = [True, False]   # near_edge_tp

MIN_GAP      = min(GAP_VALUES)
MIN_APPROACH = min(APPROACH_VALUES)
MIN_REWARD   = min(REWARD_VALUES)

TOTAL = len(GAP_VALUES) * len(APPROACH_VALUES) * len(REWARD_VALUES) * len(TP_MODES)

# ── Data loader ───────────────────────────────────────────────────────────────
def load_merged(sym: str) -> pd.DataFrame:
    path_map = {
        'DXY':    BASE / 'TVC_DXY, 15_merged.csv',
        'EURUSD': BASE / 'FX_EURUSD, 15_merged.csv',
        'USDJPY': BASE / 'FX_USDJPY, 15_merged.csv',
        'USDCAD': BASE / 'FX_USDCAD, 15_merged.csv',
        'XAUUSD': BASE / 'FX_XAUUSD, 15_merged.csv',
    }
    df = pd.read_csv(path_map[sym])
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    return df[['time', 'open', 'high', 'low', 'close']].copy()


# ── Modified signal generator that saves metadata ────────────────────────────
def generate_attr_universe(df_dxy, near_edge_tp: bool) -> list:
    """
    Run ATTR detection with minimum thresholds.
    Each signal dict gains three extra keys:
      gap_pts      – actual gap at London open (for post-hoc gap filter)
      approach_pts – actual 3-bar approach at signal bar
      reward_pts   – actual reward remaining at signal bar (for chosen TP mode)
    REV signals are NOT generated (we only need ATTR for the sweep).
    """
    # Temporarily set minimum thresholds
    r.ATTR_MIN_GAP      = MIN_GAP
    r.ATTR_APPROACH_PTS = MIN_APPROACH
    r.ATTR_MIN_REWARD   = MIN_REWARD

    df = df_dxy.copy().reset_index(drop=True)
    df['bias_1h'] = r.compute_htf_bias(df, 1)
    df['bias_4h'] = r.compute_htf_bias(df, 4)
    bull_sig, bear_sig = r.candle_signals(df)

    zone_top = zone_bottom = np.nan
    japan_bull = False
    strict_pristine = False
    attr_pristine   = False
    london_touched  = False
    attr_traded     = False
    in_trade_until  = -1
    gap_pts_at_london = 0.0   # actual gap recorded at London open each day

    signals = []
    n = len(df)

    for i in range(2, n):
        row = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']
        ts    = row['time']
        hour  = ts.hour
        minute = ts.minute
        curr_min = hour * 60 + minute
        dow   = ts.dayofweek

        is_2345 = (hour == 23) and (minute == 45)
        in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

        # Zone formation
        if is_2345:
            zt, zb, jb = r.form_zone(df, i)
            if zt is not None:
                zone_top, zone_bottom = zt, zb
                japan_bull = jb
                strict_pristine = True
                attr_pristine   = False
                london_touched  = False
                attr_traded     = False
                gap_pts_at_london = 0.0
            continue

        if np.isnan(zone_top):
            continue

        if strict_pristine and (l <= zone_top) and (h >= zone_bottom):
            strict_pristine = False

        # Evaluate attr_pristine at London open; record actual gap
        london_open_bar = (not in_japan and
                           ((dow != 0 and curr_min == r.ATTR_WINDOW[0]) or
                            (dow == 0 and curr_min == 6 * 60 + 30)))
        if london_open_bar:
            if not japan_bull:
                actual_gap = (zone_bottom - c) * 10000
            else:
                actual_gap = (c - zone_top) * 10000
            gap_pts_at_london = actual_gap
            attr_pristine = actual_gap >= MIN_GAP
            london_touched = False

        prev_london_touched = london_touched
        if not in_japan and not london_touched:
            if ((not japan_bull) and (h >= zone_bottom)) or (japan_bull and (l <= zone_top)):
                london_touched = True

        if i <= in_trade_until:
            continue

        zone_width_pts = (zone_top - zone_bottom) * 10000

        mon_start = 6 * 60 + 30
        eff_attr_start = mon_start if dow == 0 else r.ATTR_WINDOW[0]
        in_attr_sess = eff_attr_start <= curr_min <= r.ATTR_WINDOW[1] and not in_japan

        # Approach momentum
        if i >= 3:
            c_prev3 = df.at[i - 3, 'close']
            approach_pts = ((c - c_prev3) if not japan_bull else (c_prev3 - c)) * 10000
        else:
            approach_pts = 0.0

        if not (attr_pristine and not london_touched and in_attr_sess
                and zone_width_pts >= r.ZONE_MIN_WIDTH and not attr_traded):
            continue

        if approach_pts < MIN_APPROACH:
            continue

        # ATTR LONG
        if not japan_bull and bull_sig.at[i]:
            reward_far  = (zone_top    - c) * 10000
            reward_near = (zone_bottom + r.ATTR_NEAR_BUFFER / 10000 - c) * 10000

            reward_pts = reward_near if near_edge_tp else reward_far
            if reward_pts < MIN_REWARD:
                continue

            tp_price = (zone_bottom + r.ATTR_NEAR_BUFFER / 10000
                        if near_edge_tp else zone_top)
            sl_d = tp_price - c
            if sl_d <= 0:
                continue
            sl_price = c - sl_d
            outcome, exit_px, exit_bar = r.resolve(df, i, c, tp_price, sl_price, 'long')

            signals.append({
                'type': 'ATTR_LONG', 'entry_time': str(ts),
                'entry': round(c, 5), 'tp': round(tp_price, 5), 'sl': round(sl_price, 5),
                'sl_pts': round(sl_d * 10000), 'tp_pts': round(sl_d * 10000),
                'zone_top': round(zone_top, 5), 'zone_bottom': round(zone_bottom, 5),
                'zone_width': round(zone_width_pts),
                'pristine': True, 'outcome': outcome,
                'exit_px': round(exit_px, 5),
                'exit_time': str(df.at[exit_bar, 'time']),
                'bias_1h': int(row['bias_1h']), 'bias_4h': int(row['bias_4h']),
                # metadata for post-hoc filtering
                'gap_pts': round(gap_pts_at_london, 1),
                'approach_pts_meta': round(approach_pts, 1),
                'reward_pts': round(reward_pts, 1),
            })
            attr_traded = True
            in_trade_until = exit_bar
            continue

        # ATTR SHORT
        if japan_bull and bear_sig.at[i]:
            reward_far  = (c - zone_bottom) * 10000
            reward_near = (c - zone_top + r.ATTR_NEAR_BUFFER / 10000) * 10000

            reward_pts = reward_near if near_edge_tp else reward_far
            if reward_pts < MIN_REWARD:
                continue

            tp_price = (zone_top - r.ATTR_NEAR_BUFFER / 10000
                        if near_edge_tp else zone_bottom)
            sl_d = c - tp_price
            if sl_d <= 0:
                continue
            sl_price = c + sl_d
            outcome, exit_px, exit_bar = r.resolve(df, i, c, tp_price, sl_price, 'short')

            signals.append({
                'type': 'ATTR_SHORT', 'entry_time': str(ts),
                'entry': round(c, 5), 'tp': round(tp_price, 5), 'sl': round(sl_price, 5),
                'sl_pts': round(sl_d * 10000), 'tp_pts': round(sl_d * 10000),
                'zone_top': round(zone_top, 5), 'zone_bottom': round(zone_bottom, 5),
                'zone_width': round(zone_width_pts),
                'pristine': True, 'outcome': outcome,
                'exit_px': round(exit_px, 5),
                'exit_time': str(df.at[exit_bar, 'time']),
                'bias_1h': int(row['bias_1h']), 'bias_4h': int(row['bias_4h']),
                'gap_pts': round(gap_pts_at_london, 1),
                'approach_pts_meta': round(approach_pts, 1),
                'reward_pts': round(reward_pts, 1),
            })
            attr_traded = True
            in_trade_until = exit_bar

    return signals


# ── Fast combo evaluator ──────────────────────────────────────────────────────
def eval_combo(universe, pair_dfs, gap, approach, reward):
    """Filter pre-generated universe and apply DXY-exit. Returns (n, pair_nets, port)."""
    filtered = [s for s in universe
                if s['gap_pts']           >= gap
                and s['approach_pts_meta'] >= approach
                and s['reward_pts']        >= reward]

    if not filtered:
        return 0, {}, None

    all_trades = []
    pair_nets  = {}
    for pair in PAIRS:
        trades = r.apply_to_pair_dxy_exit(filtered, pair_dfs[pair], pair)
        all_trades.extend(trades)
        s = r.stats_r(trades, pair)
        pair_nets[pair] = round(s.get('NetR', 0), 2)

    port = r.stats_r(all_trades, 'PORT')
    return len(filtered), pair_nets, port


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("Loading 33-month merged data...")
    df_dxy   = load_merged('DXY')
    pair_dfs = {p: load_merged(p) for p in PAIRS}
    print(f"  DXY bars : {len(df_dxy)}")
    print(f"  Combos   : {TOTAL}")
    print()

    # ── Pre-generate universes (2 slow runs) ──────────────────────────────────
    print("Pre-generating ATTR universe  [near_edge_tp=False]  ...")
    universe_far  = generate_attr_universe(df_dxy, near_edge_tp=False)
    print(f"  Universe (far-side TP) : {len(universe_far)} signals")

    print("Pre-generating ATTR universe  [near_edge_tp=True]   ...")
    universe_near = generate_attr_universe(df_dxy, near_edge_tp=True)
    print(f"  Universe (near-edge TP): {len(universe_near)} signals")
    print()

    # ── Sweep ─────────────────────────────────────────────────────────────────
    results = []
    done    = 0

    for gap, approach, reward, near_tp in itertools.product(
            GAP_VALUES, APPROACH_VALUES, REWARD_VALUES, TP_MODES):

        universe = universe_near if near_tp else universe_far
        n_sigs, pair_nets, port = eval_combo(universe, pair_dfs, gap, approach, reward)
        done += 1

        if port is None:
            results.append({
                'gap': gap, 'approach': approach, 'reward': reward,
                'near_tp': near_tp, 'n_sigs': 0,
                'net_r': -999, 'wr': 0, 'pf': 0,
                **{f'net_{p.lower()}': 0 for p in PAIRS}
            })
        else:
            results.append({
                'gap':      gap,
                'approach': approach,
                'reward':   reward,
                'near_tp':  near_tp,
                'n_sigs':   n_sigs,
                'net_r':    round(port.get('NetR', 0), 2),
                'wr':       round(port.get('WR%', 0), 1),
                'pf':       round(port.get('PF', 0), 3),
                **{f'net_{p.lower()}': pair_nets.get(p, 0) for p in PAIRS}
            })

        if done % 100 == 0:
            best = max(results, key=lambda x: x['net_r'])
            print(f"  [{done:>4}/{TOTAL}]  best so far: "
                  f"gap={best['gap']}  approach={best['approach']}  "
                  f"reward={best['reward']}  near_tp={best['near_tp']}  "
                  f"NetR={best['net_r']:+.1f}R  (n={best['n_sigs']})")

    # ── Save ──────────────────────────────────────────────────────────────────
    df = pd.DataFrame(results)
    out = BASE / 'sweep_attr_results.csv'
    df.to_csv(out, index=False)
    print(f"\n  Saved {len(df)} rows → {out.name}")

    # ── Top 30 ────────────────────────────────────────────────────────────────
    top = df[df['n_sigs'] >= 5].sort_values('net_r', ascending=False).head(30)

    print()
    print("=" * 95)
    print("  TOP 30 ATTR COMBOS  (min 5 signals, sorted by portfolio NetR, DXY-exit)")
    print("=" * 95)
    print(f"  {'GAP':>5} {'APPR':>5} {'RWD':>5} {'TP':>8}  {'N':>4}  "
          f"{'WR%':>6}  {'PF':>6}  {'NetR':>8}  "
          + "  ".join(f"{p[:6]:>8}" for p in PAIRS))
    print(f"  {'-'*92}")

    for _, row in top.iterrows():
        tp_str    = "near" if row['near_tp'] else "far"
        pair_cols = "  ".join(f"{row[f'net_{p.lower()}']:>+8.1f}" for p in PAIRS)
        print(f"  {int(row['gap']):>5} {int(row['approach']):>5} {int(row['reward']):>5} "
              f"{tp_str:>8}  {int(row['n_sigs']):>4}  "
              f"{row['wr']:>5.1f}%  {row['pf']:>6.3f}  {row['net_r']:>+8.1f}R  {pair_cols}")

    # ── Current baseline ──────────────────────────────────────────────────────
    print()
    print("  Current params: gap=75  approach=150  reward=100  near_tp=True")
    base = df[(df['gap'] == 75) & (df['approach'] == 150) &
              (df['reward'] == 100) & (df['near_tp'] == True)]
    if not base.empty:
        row = base.iloc[0]
        print(f"  Current ATTR NetR: {row['net_r']:+.1f}R  "
              f"(n={int(row['n_sigs'])}  WR={row['wr']}%)")

    # ── Best per TP mode ──────────────────────────────────────────────────────
    print()
    for tp_mode, label in [(True, "near-edge TP"), (False, "far-side TP")]:
        sub = df[(df['near_tp'] == tp_mode) & (df['n_sigs'] >= 5)]
        if sub.empty:
            continue
        best = sub.loc[sub['net_r'].idxmax()]
        print(f"  Best {label}: gap={int(best['gap'])}  approach={int(best['approach'])}  "
              f"reward={int(best['reward'])}  →  "
              f"N={int(best['n_sigs'])}  WR={best['wr']}%  NetR={best['net_r']:+.1f}R")

    # ── Frequency sanity check ────────────────────────────────────────────────
    print()
    print("  Brice target: ~1 ATTR/month over 33 months = ~33 signals total")
    for tp_mode, label in [(True, "near"), (False, "far")]:
        sub = df[df['near_tp'] == tp_mode]
        closest = sub.iloc[(sub['n_sigs'] - 33).abs().argsort()[:1]]
        if not closest.empty:
            row = closest.iloc[0]
            print(f"  Closest-to-33 ({label}): gap={int(row['gap'])}  "
                  f"approach={int(row['approach'])}  reward={int(row['reward'])}  "
                  f"n={int(row['n_sigs'])}  NetR={row['net_r']:+.1f}R")


if __name__ == '__main__':
    main()
