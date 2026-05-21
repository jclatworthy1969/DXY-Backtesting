"""
sweep_attr.py
ATTR-only parameter sweep on 33-month merged data.
Holds REV params fixed; varies ATTR_MIN_GAP, ATTR_APPROACH_PTS,
ATTR_MIN_REWARD and TP mode (near-edge vs far-side).

Reports top 30 combos by ATTR portfolio NetR (DXY-exit method).
"""

import pandas as pd
import numpy as np
import itertools
from pathlib import Path
import dxy_clean_rules as r

BASE  = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
PAIRS = r.PAIRS

# ── Sweep grid ────────────────────────────────────────────────────────────────
GAP_VALUES      = [50, 75, 100, 125, 150, 175, 200]   # ATTR_MIN_GAP
APPROACH_VALUES = [50, 75, 100, 125, 150, 175, 200]   # ATTR_APPROACH_PTS
REWARD_VALUES   = [50, 75, 100, 125, 150, 175, 200]   # ATTR_MIN_REWARD
TP_MODES        = [True, False]                         # near_edge_tp

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


# ── Patched signal generator (ATTR params only) ───────────────────────────────
def run_attr_sweep(df_dxy, pair_dfs, gap, approach, reward, near_edge_tp):
    """
    Temporarily override ATTR params, generate signals, filter to ATTR only,
    apply DXY-exit to all pairs. Returns (n_sigs, pair_results_dict, portfolio_stats).
    """
    # Patch module-level constants
    r.ATTR_MIN_GAP      = gap
    r.ATTR_APPROACH_PTS = approach
    r.ATTR_MIN_REWARD   = reward

    sigs, _ = r.generate_dxy_signals(df_dxy, near_edge_tp=near_edge_tp)
    attr_sigs = [s for s in sigs if 'ATTR' in s['type']]

    if not attr_sigs:
        return 0, {}, None

    all_trades = []
    pair_nets  = {}
    for pair in PAIRS:
        trades = r.apply_to_pair_dxy_exit(attr_sigs, pair_dfs[pair], pair)
        all_trades.extend(trades)
        s = r.stats_r(trades, pair)
        pair_nets[pair] = round(s.get('NetR', 0), 2)

    port = r.stats_r(all_trades, 'PORT')
    return len(attr_sigs), pair_nets, port


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("Loading 33-month merged data...")
    df_dxy   = load_merged('DXY')
    pair_dfs = {p: load_merged(p) for p in PAIRS}
    print(f"  DXY bars: {len(df_dxy)}")
    print(f"  Total combos: {TOTAL}")
    print()

    results = []
    done = 0

    for gap, approach, reward, near_tp in itertools.product(
            GAP_VALUES, APPROACH_VALUES, REWARD_VALUES, TP_MODES):

        n_sigs, pair_nets, port = run_attr_sweep(
            df_dxy, pair_dfs, gap, approach, reward, near_tp)
        done += 1

        if port is None:
            results.append({
                'gap': gap, 'approach': approach, 'reward': reward,
                'near_tp': near_tp, 'n_sigs': 0,
                'net_r': -999, 'wr': 0, 'pf': 0,
                **{f'net_{p.lower()}': 0 for p in PAIRS}
            })
            continue

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

        if done % 50 == 0:
            best_so_far = max(results, key=lambda x: x['net_r'])
            print(f"  [{done:>4}/{TOTAL}]  best so far: "
                  f"gap={best_so_far['gap']}  approach={best_so_far['approach']}  "
                  f"reward={best_so_far['reward']}  near_tp={best_so_far['near_tp']}  "
                  f"NetR={best_so_far['net_r']:+.1f}R  (n={best_so_far['n_sigs']})")

    # ── Save results ─────────────────────────────────────────────────────────
    df = pd.DataFrame(results)
    out = BASE / 'sweep_attr_results.csv'
    df.to_csv(out, index=False)
    print(f"\n  Saved {len(df)} rows to {out.name}")

    # ── Top 30 by portfolio NetR ──────────────────────────────────────────────
    top = df[df['n_sigs'] >= 10].sort_values('net_r', ascending=False).head(30)

    print()
    print("=" * 90)
    print("  TOP 30 ATTR COMBOS  (min 10 signals, sorted by portfolio NetR, DXY-exit)")
    print("=" * 90)
    print(f"  {'GAP':>5} {'APPR':>5} {'RWD':>5} {'TP':>8}  {'N':>4}  "
          f"{'WR%':>6}  {'PF':>6}  {'NetR':>8}  "
          + "  ".join(f"{p[:6]:>8}" for p in PAIRS))
    print(f"  {'-'*88}")

    for _, row in top.iterrows():
        tp_str = "near" if row['near_tp'] else "far"
        pair_cols = "  ".join(f"{row[f'net_{p.lower()}']:>+8.1f}" for p in PAIRS)
        print(f"  {int(row['gap']):>5} {int(row['approach']):>5} {int(row['reward']):>5} "
              f"{tp_str:>8}  {int(row['n_sigs']):>4}  "
              f"{row['wr']:>5.1f}%  {row['pf']:>6.3f}  {row['net_r']:>+8.1f}R  {pair_cols}")

    # ── Baseline reminder ─────────────────────────────────────────────────────
    print()
    print("  Current params: gap=75  approach=150  reward=100  near_tp=True")
    base = df[(df['gap'] == 75) & (df['approach'] == 150) &
              (df['reward'] == 100) & (df['near_tp'] == True)]
    if not base.empty:
        row = base.iloc[0]
        print(f"  Current ATTR NetR: {row['net_r']:+.1f}R  (n={int(row['n_sigs'])}  WR={row['wr']}%)")

    # ── Best per TP mode ──────────────────────────────────────────────────────
    print()
    for tp_mode, label in [(True, "near-edge TP"), (False, "far-side TP")]:
        sub = df[(df['near_tp'] == tp_mode) & (df['n_sigs'] >= 10)]
        if sub.empty:
            continue
        best = sub.loc[sub['net_r'].idxmax()]
        print(f"  Best {label}: gap={int(best['gap'])}  approach={int(best['approach'])}  "
              f"reward={int(best['reward'])}  ->  "
              f"N={int(best['n_sigs'])}  WR={best['wr']}%  NetR={best['net_r']:+.1f}R")


if __name__ == '__main__':
    main()
