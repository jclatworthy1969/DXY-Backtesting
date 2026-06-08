"""
update_dxy_webhooks.py
======================
Adds JSON webhook support to both DXY Pine Script indicators.

DXYTradeAlert.pine  — adds DXY_ENTRY and DXY_EXIT alert() calls alongside
                       the existing alertcondition() human notifications.

DXYPairLevels.pine  — adds SL_MOVE breakeven advisory event (new).
                       ENTRY / EXIT webhooks already present and unchanged.

Run once then apply updated .pine files to TradingView manually.
"""

from pathlib import Path

BASE = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

# ─────────────────────────────────────────────────────────────────────────────
#  DXYTradeAlert.pine
# ─────────────────────────────────────────────────────────────────────────────

alert_path = BASE / "DXYTradeAlert.pine"
src = alert_path.read_text(encoding="utf-8")
original_len = len(src)

# 1. Add grp_wh after grp_disp
OLD = 'grp_disp = "Display"'
NEW = 'grp_disp = "Display"\ngrp_wh   = "Webhook / Alerts"'
assert src.count(OLD) == 1, f"Expected 1 occurrence of grp_disp line, found {src.count(OLD)}"
src = src.replace(OLD, NEW, 1)

# 2. Add enable_webhook input after show_exit_lvls
OLD = 'show_exit_lvls = input.bool(true, "Show Exit TP/SL Lines",           group=grp_disp)'
NEW = (
    'show_exit_lvls = input.bool(true, "Show Exit TP/SL Lines",           group=grp_disp)\n'
    '\n'
    '// Webhook\n'
    'enable_webhook = input.bool(false, "Enable DXY Webhook Alerts", group=grp_wh,\n'
    '    tooltip="Fires alert() JSON on every DXY entry signal and DXY TP/SL exit.\\n'
    "Create ONE TradingView alert on this indicator with the 'alert() function calls' condition.\\n"
    'DXY_ENTRY: notifies automation of active DXY direction and key levels.\\n'
    'DXY_EXIT: tells automation to close ALL open pair positions immediately.\\n'
    'Also enable webhooks on each DXY Pair Levels chart for per-pair ENTRY/EXIT/SL_MOVE events.")'
)
assert src.count(OLD) == 1
src = src.replace(OLD, NEW, 1)

# 3. Add var string sig_name after exit_entry_bar
OLD = 'var int   exit_entry_bar = -1'
NEW = (
    'var int   exit_entry_bar = -1\n'
    'var string sig_name      = ""       // last fired signal — used in DXY_EXIT webhook'
)
assert src.count(OLD) == 1
src = src.replace(OLD, NEW, 1)

# 4. Replace the entire "Register TP/SL when entry fires" block with enhanced version
# This single replacement covers all 7 entry signal tracking blocks
OLD = """\
// Register TP/SL when entry fires
if gap_rej_long
    exit_tp_lvl   := attr_long_tp
    exit_sl_lvl   := attr_long_sl
    exit_tracking := true
    exit_is_long  := true
    exit_entry_bar := bar_index

if gap_rej_short
    exit_tp_lvl   := attr_short_tp
    exit_sl_lvl   := attr_short_sl
    exit_tracking := true
    exit_is_long  := false
    exit_entry_bar := bar_index

if rev_long
    exit_tp_lvl   := rev_long_tp
    exit_sl_lvl   := rev_long_sl
    exit_tracking := true
    exit_is_long  := true
    exit_entry_bar := bar_index

if rev_short
    exit_tp_lvl   := rev_short_tp
    exit_sl_lvl   := rev_short_sl
    exit_tracking := true
    exit_is_long  := false
    exit_entry_bar := bar_index

if lon_attr_long
    exit_tp_lvl    := lon_attr_long_tp
    exit_sl_lvl    := lon_attr_long_sl
    exit_tracking  := true
    exit_is_long   := true
    exit_entry_bar := bar_index

if attr_core_long
    exit_tp_lvl    := attr_long_tp
    exit_sl_lvl    := attr_long_sl
    exit_tracking  := true
    exit_is_long   := true
    exit_entry_bar := bar_index

if attr_core_short
    exit_tp_lvl    := attr_short_tp
    exit_sl_lvl    := attr_short_sl
    exit_tracking  := true
    exit_is_long   := false
    exit_entry_bar := bar_index"""

NEW = """\
// Register TP/SL when entry fires
if gap_rej_long
    exit_tp_lvl   := attr_long_tp
    exit_sl_lvl   := attr_long_sl
    exit_tracking := true
    exit_is_long  := true
    exit_entry_bar := bar_index
    sig_name      := "GAP_REJ_LONG"
    if enable_webhook
        alert('{"event":"DXY_ENTRY","signal":"GAP_REJ_LONG","dxy_dir":"LONG","dxy_entry":' + str.tostring(close) + ',"dxy_tp":' + str.tostring(attr_long_tp) + ',"dxy_sl":' + str.tostring(attr_long_sl) + ',"sl_pts":' + str.tostring(math.round(attr_long_sl_d * 10000)) + ',"time":"' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + '"}', alert.freq_once_per_bar)

if gap_rej_short
    exit_tp_lvl   := attr_short_tp
    exit_sl_lvl   := attr_short_sl
    exit_tracking := true
    exit_is_long  := false
    exit_entry_bar := bar_index
    sig_name      := "GAP_REJ_SHORT"
    if enable_webhook
        alert('{"event":"DXY_ENTRY","signal":"GAP_REJ_SHORT","dxy_dir":"SHORT","dxy_entry":' + str.tostring(close) + ',"dxy_tp":' + str.tostring(attr_short_tp) + ',"dxy_sl":' + str.tostring(attr_short_sl) + ',"sl_pts":' + str.tostring(math.round(attr_short_sl_d * 10000)) + ',"time":"' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + '"}', alert.freq_once_per_bar)

if rev_long
    exit_tp_lvl   := rev_long_tp
    exit_sl_lvl   := rev_long_sl
    exit_tracking := true
    exit_is_long  := true
    exit_entry_bar := bar_index
    sig_name      := "REV_LONG"
    if enable_webhook
        alert('{"event":"DXY_ENTRY","signal":"REV_LONG","dxy_dir":"LONG","dxy_entry":' + str.tostring(close) + ',"dxy_tp":' + str.tostring(rev_long_tp) + ',"dxy_sl":' + str.tostring(rev_long_sl) + ',"sl_pts":' + str.tostring(math.round(rev_long_sl_d * 10000)) + ',"time":"' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + '"}', alert.freq_once_per_bar)

if rev_short
    exit_tp_lvl   := rev_short_tp
    exit_sl_lvl   := rev_short_sl
    exit_tracking := true
    exit_is_long  := false
    exit_entry_bar := bar_index
    sig_name      := "REV_SHORT"
    if enable_webhook
        alert('{"event":"DXY_ENTRY","signal":"REV_SHORT","dxy_dir":"SHORT","dxy_entry":' + str.tostring(close) + ',"dxy_tp":' + str.tostring(rev_short_tp) + ',"dxy_sl":' + str.tostring(rev_short_sl) + ',"sl_pts":' + str.tostring(math.round(rev_short_sl_d * 10000)) + ',"time":"' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + '"}', alert.freq_once_per_bar)

if lon_attr_long
    exit_tp_lvl    := lon_attr_long_tp
    exit_sl_lvl    := lon_attr_long_sl
    exit_tracking  := true
    exit_is_long   := true
    exit_entry_bar := bar_index
    sig_name       := "LON_ATTR_LONG"
    if enable_webhook
        alert('{"event":"DXY_ENTRY","signal":"LON_ATTR_LONG","dxy_dir":"LONG","dxy_entry":' + str.tostring(close) + ',"dxy_tp":' + str.tostring(lon_attr_long_tp) + ',"dxy_sl":' + str.tostring(lon_attr_long_sl) + ',"sl_pts":' + str.tostring(math.round(lon_attr_long_sl_d * 10000)) + ',"time":"' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + '"}', alert.freq_once_per_bar)

if attr_core_long
    exit_tp_lvl    := attr_long_tp
    exit_sl_lvl    := attr_long_sl
    exit_tracking  := true
    exit_is_long   := true
    exit_entry_bar := bar_index
    sig_name       := "ATTR_CORE_LONG"
    if enable_webhook
        alert('{"event":"DXY_ENTRY","signal":"ATTR_CORE_LONG","dxy_dir":"LONG","dxy_entry":' + str.tostring(close) + ',"dxy_tp":' + str.tostring(attr_long_tp) + ',"dxy_sl":' + str.tostring(attr_long_sl) + ',"sl_pts":' + str.tostring(math.round(attr_long_sl_d * 10000)) + ',"gap_at_lon":' + str.tostring(math.round(attr_gap_at_lon)) + ',"wave_ext":' + str.tostring(math.round(attr_wave_ext)) + ',"time":"' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + '"}', alert.freq_once_per_bar)

if attr_core_short
    exit_tp_lvl    := attr_short_tp
    exit_sl_lvl    := attr_short_sl
    exit_tracking  := true
    exit_is_long   := false
    exit_entry_bar := bar_index
    sig_name       := "ATTR_CORE_SHORT"
    if enable_webhook
        alert('{"event":"DXY_ENTRY","signal":"ATTR_CORE_SHORT","dxy_dir":"SHORT","dxy_entry":' + str.tostring(close) + ',"dxy_tp":' + str.tostring(attr_short_tp) + ',"dxy_sl":' + str.tostring(attr_short_sl) + ',"sl_pts":' + str.tostring(math.round(attr_short_sl_d * 10000)) + ',"gap_at_lon":' + str.tostring(math.round(attr_gap_at_lon)) + ',"wave_ext":' + str.tostring(math.round(attr_wave_ext)) + ',"time":"' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + '"}', alert.freq_once_per_bar)"""

assert src.count(OLD) == 1, f"Exit tracking block not found uniquely"
src = src.replace(OLD, NEW, 1)

# 5. Add DXY_EXIT webhook in TP label block
OLD = (
    '    label.new(bar_index, exit_is_long ? high + lbl_pad : low - lbl_pad,\n'
    '        "▲ DXY TP HIT\\nExit pair positions",\n'
    '        color=color.new(color.lime, 10), textcolor=color.black,\n'
    '        style=exit_is_long ? label.style_label_down : label.style_label_up, size=size.normal)'
)
NEW = (
    '    label.new(bar_index, exit_is_long ? high + lbl_pad : low - lbl_pad,\n'
    '        "▲ DXY TP HIT\\nExit pair positions",\n'
    '        color=color.new(color.lime, 10), textcolor=color.black,\n'
    '        style=exit_is_long ? label.style_label_down : label.style_label_up, size=size.normal)\n'
    '    if enable_webhook\n'
    '        alert(\'{"event":"DXY_EXIT","result":"TP","signal":"\' + sig_name + \'","dxy_exit":\' + str.tostring(close) + \',"time":"\' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + \'"}\', alert.freq_once_per_bar)'
)
assert src.count(OLD) == 1
src = src.replace(OLD, NEW, 1)

# 6. Add DXY_EXIT webhook in SL label block
OLD = (
    '    label.new(bar_index, exit_is_long ? low - lbl_pad : high + lbl_pad,\n'
    '        "▼ DXY SL HIT\\nExit pair positions",\n'
    '        color=color.new(color.red, 10), textcolor=color.white,\n'
    '        style=exit_is_long ? label.style_label_up : label.style_label_down, size=size.normal)'
)
NEW = (
    '    label.new(bar_index, exit_is_long ? low - lbl_pad : high + lbl_pad,\n'
    '        "▼ DXY SL HIT\\nExit pair positions",\n'
    '        color=color.new(color.red, 10), textcolor=color.white,\n'
    '        style=exit_is_long ? label.style_label_up : label.style_label_down, size=size.normal)\n'
    '    if enable_webhook\n'
    '        alert(\'{"event":"DXY_EXIT","result":"SL","signal":"\' + sig_name + \'","dxy_exit":\' + str.tostring(close) + \',"time":"\' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + \'"}\', alert.freq_once_per_bar)'
)
assert src.count(OLD) == 1
src = src.replace(OLD, NEW, 1)

# 7. Clear sig_name in the state clear block (alongside exit_tracking)
OLD = 'if dxy_tp_hit or dxy_sl_hit\n    exit_tracking := false\n    line.delete(tp_line)\n    line.delete(sl_line)'
NEW = 'if dxy_tp_hit or dxy_sl_hit\n    exit_tracking := false\n    sig_name      := ""\n    line.delete(tp_line)\n    line.delete(sl_line)'
assert src.count(OLD) == 1
src = src.replace(OLD, NEW, 1)

alert_path.write_text(src, encoding="utf-8")
added = len(src) - original_len
print(f"DXYTradeAlert.pine  updated: {len(src):,} chars (+{added} added)")

# ─────────────────────────────────────────────────────────────────────────────
#  DXYPairLevels.pine
# ─────────────────────────────────────────────────────────────────────────────

pair_path = BASE / "DXYPairLevels.pine"
src = pair_path.read_text(encoding="utf-8")
original_len = len(src)

# 1. Add be_trigger_pct input after enable_webhook
OLD = (
    'enable_webhook = input.bool(false, "Enable Webhook Alerts", group=grp_wh,\n'
    '    tooltip="Fires alert() JSON on every entry and exit.\\nCreate ONE alert on this indicator in TradingView with the \'alert() function calls\' condition.\\nPoint the notification to your webhook URL.")'
)
NEW = (
    'enable_webhook = input.bool(false, "Enable Webhook Alerts", group=grp_wh,\n'
    '    tooltip="Fires alert() JSON on every entry and exit.\\nCreate ONE alert on this indicator in TradingView with the \'alert() function calls\' condition.\\nPoint the notification to your webhook URL.")\n'
    'be_trigger_pct = input.int(50, "Breakeven Move — Trigger (% toward TP)", group=grp_wh,\n'
    '    minval=25, maxval=75, step=5,\n'
    '    tooltip="Fire an advisory SL_MOVE webhook when DXY has moved this % of the way from entry to TP. Default 50% = halfway. IMPORTANT: SL_MOVE is advisory only — never place automatic orders based on this event.")'
)
assert src.count(OLD) == 1
src = src.replace(OLD, NEW, 1)

# 2. Add 3 new state vars after sig_name declaration
OLD = 'var string sig_name      = ""       // signal name for webhook/labels'
NEW = (
    'var string sig_name      = ""       // signal name for webhook/labels\n'
    'var float dxy_entry_price = na       // DXY close at entry bar (SL_MOVE progress calc)\n'
    'var float pair_entry_px   = na       // pair close at entry bar (= breakeven SL level)\n'
    'var bool  sl_move_fired   = false    // prevents duplicate SL_MOVE per trade'
)
assert src.count(OLD) == 1
src = src.replace(OLD, NEW, 1)

# 3. Add new var assignments in all 7 entry blocks (each has a unique sig_name := "..." line)
ENTRY_SIGS = [
    'sig_name        := "GAP_REJ_LONG"',
    'sig_name        := "GAP_REJ_SHORT"',
    'sig_name        := "REV_LONG"',
    'sig_name        := "REV_SHORT"',
    'sig_name        := "LON_ATTR_LONG"',
    'sig_name        := "ATTR_CORE_LONG"',
    'sig_name        := "ATTR_CORE_SHORT"',
]
for sig_line in ENTRY_SIGS:
    assert src.count(sig_line) == 1, f"Expected 1 occurrence of: {sig_line}"
    src = src.replace(
        sig_line,
        sig_line + "\n"
        "    dxy_entry_price := dxy_c\n"
        "    pair_entry_px   := close\n"
        "    sl_move_fired   := false",
        1
    )

# 4. Add SL_MOVE detection block before the DXY EXIT section
SL_MOVE_BLOCK = """\
// ─── SL_MOVE: BREAKEVEN ADVISORY ─────────────────────────────────────────────
// Fires when DXY has moved be_trigger_pct % of the way from entry to TP.
// Advisory only — tells automation to consider moving pair stop to breakeven.
// NEVER act on SL_MOVE automatically.
if dxy_exit_track and enable_webhook and not sl_move_fired and barstate.isconfirmed
    if not na(dxy_entry_price) and not na(dxy_tp_lvl) and bar_index > dxy_entry_bar
        float _tp_dist  = math.abs(dxy_tp_lvl - dxy_entry_price)
        bool  _toward   = dxy_trade_long ? dxy_c > dxy_entry_price : dxy_c < dxy_entry_price
        float _progress = (_tp_dist > 0 and _toward) ? math.abs(dxy_c - dxy_entry_price) / _tp_dist * 100.0 : 0.0
        if _progress >= be_trigger_pct
            sl_move_fired := true
            alert('{"event":"SL_MOVE","pair":"' + syminfo.ticker + '","signal":"' + sig_name + '","action":"MOVE_TO_BE","new_sl":' + str.tostring(pair_entry_px, format.mintick) + ',"pair_entry":' + str.tostring(pair_entry_px, format.mintick) + ',"dxy_price":' + str.tostring(dxy_c) + ',"progress_pct":' + str.tostring(math.round(_progress)) + ',"time":"' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + '"}', alert.freq_once_per_bar)

"""

OLD = "// ─── DXY EXIT ────────────────────────────────────────────────────────────────\n"
assert src.count(OLD) == 1
src = src.replace(OLD, SL_MOVE_BLOCK + OLD, 1)

# 5. Reset new vars in the exit state clear block
OLD = """\
        // Reset state
        dxy_exit_track := false
        dxy_tp_lvl     := na
        dxy_sl_lvl     := na
        pair_tp_lvl    := na
        pair_sl_lvl    := na
        tp_line_ref    := na
        sl_line_ref    := na
        sig_name       := ""\
"""
NEW = """\
        // Reset state
        dxy_exit_track  := false
        dxy_tp_lvl      := na
        dxy_sl_lvl      := na
        pair_tp_lvl     := na
        pair_sl_lvl     := na
        tp_line_ref     := na
        sl_line_ref     := na
        sig_name        := ""
        sl_move_fired   := false
        dxy_entry_price := na
        pair_entry_px   := na\
"""
assert src.count(OLD) == 1
src = src.replace(OLD, NEW, 1)

# 6. Reset new vars in Friday reset (sig_name := "" followed by dxy_london_open)
OLD = '    sig_name          := ""\n    dxy_london_open   := na'
NEW = (
    '    sig_name          := ""\n'
    '    sl_move_fired     := false\n'
    '    dxy_entry_price   := na\n'
    '    pair_entry_px     := na\n'
    '    dxy_london_open   := na'
)
assert src.count(OLD) == 1
src = src.replace(OLD, NEW, 1)

pair_path.write_text(src, encoding="utf-8")
added = len(src) - original_len
print(f"DXYPairLevels.pine  updated: {len(src):,} chars (+{added} added)")

print("\nBoth Pine Script files updated successfully.")
print("Apply updated .pine files to TradingView manually.")
