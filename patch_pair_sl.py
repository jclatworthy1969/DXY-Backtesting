"""Inserts PAIR_SL monitoring block into DXYPairLevels.pine."""

src = open('DXYPairLevels.pine', encoding='utf-8').read()

PAIR_SL_BLOCK = (
    "\n"
    "// ─── PAIR SL: HARD STOP ON PAIR CHART ────────────────────────────────────────\n"
    "// Pair hard stop-loss hit before DXY exits. Caps loss at exactly -1R and\n"
    "// terminates trade tracking so the DXY EXIT block does not fire afterwards.\n"
    "if dxy_exit_track and not na(pair_sl_lvl) and bar_index > dxy_entry_bar and barstate.isconfirmed\n"
    "    bool pair_long   = (dxy_trade_long and pair_dir > 0) or (not dxy_trade_long and pair_dir < 0)\n"
    "    bool pair_sl_hit = pair_long ? low <= pair_sl_lvl : high >= pair_sl_lvl\n"
    "\n"
    "    if pair_sl_hit\n"
    "        if show_tp_sl and not na(tp_line_ref)\n"
    "            line.set_x2(tp_line_ref, bar_index)\n"
    "            line.set_x2(sl_line_ref, bar_index)\n"
    "\n"
    "        label.new(bar_index, pair_sl_lvl,\n"
    '            "✘ PAIR SL\\n" + sig_name + "\\n" + str.tostring(pair_sl_lvl, format.mintick),\n'
    "            color=color.new(color.red, 10), textcolor=color.white,\n"
    "            style=pair_long ? label.style_label_up : label.style_label_down, size=size.small)\n"
    "\n"
    "        if enable_webhook\n"
    '            alert(\'{"event":"PAIR_SL","pair":"\' + syminfo.ticker + \'","signal":"\' + sig_name + \'","result":"SL","exit_price":\' + str.tostring(pair_sl_lvl, format.mintick) + \',"r_result":-1.0,"time":"\' + str.format_time(time, "yyyy-MM-dd HH:mm", "UTC") + \'"}\', alert.freq_once_per_bar)\n'
    "\n"
    "        dxy_exit_track  := false\n"
    "        dxy_tp_lvl      := na\n"
    "        dxy_sl_lvl      := na\n"
    "        pair_tp_lvl     := na\n"
    "        pair_sl_lvl     := na\n"
    "        tp_line_ref     := na\n"
    "        sl_line_ref     := na\n"
    '        sig_name        := ""\n'
    "        sl_move_fired   := false\n"
    "        dxy_entry_price := na\n"
    "        pair_entry_px   := na\n"
    "\n"
)

# Find the DXY EXIT comment (starts with the box-drawing characters)
DXY_EXIT_COMMENT = "// ─── DXY EXIT"
count = src.count(DXY_EXIT_COMMENT)
assert count == 1, f"Expected 1 match, found {count}"

new_src = src.replace(DXY_EXIT_COMMENT, PAIR_SL_BLOCK + DXY_EXIT_COMMENT)
open('DXYPairLevels.pine', 'w', encoding='utf-8').write(new_src)

chars_added = len(new_src) - len(src)
print(f"Done. Added {chars_added} chars. New file: {len(new_src)} chars.")

# Verify it looks right
idx = new_src.find("PAIR SL: HARD STOP")
print("\nContext around insertion:")
print(new_src[idx-50:idx+600])
