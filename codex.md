## User
Read agent_instructions.md and follow it strictly.
Fix the project reopen bug in app/main_window.py so that saved channel_groups are restored before rebuilding filtered data from source.
Keep the change minimal and explain exactly what you changed.

## Assistant
Adjusted the project reopen flow in [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L871) so `saved_channel_groups` are restored before `_rebuild_active_raw_from_source()` runs.

Exactly what changed:
- Moved `self._restore_channel_groups(saved_channel_groups)` to immediately after the `review` section is parsed and before `_push_scope_profile_to_ui()`, `_rebuild_active_raw_from_source()`, and `_refresh_active_signal_everywhere()`.
- Removed the later duplicate `self._restore_channel_groups(saved_channel_groups)` from the `_restoring_project` block.

This keeps the patch minimal and fixes the reopen bug by ensuring group-specific filters rebuild against the restored macro/micro assignments instead of the temporary default all-macro state.

I verified the change with a local diff; I did not run the GUI.

## User
Read agent_instructions.md and follow it strictly.

Fix exactly these two issues and nothing else:
1. group-aware UI/group lookup mismatch in non-monopolar displays
2. incorrect menu object tracking in menus.py for enable/disable state

Requirements:
- keep the patch minimal
- preserve existing behavior where possible
- do not refactor unrelated code
- after editing, explain the root cause and the exact fix
- list any assumptions if behavior is ambiguous

## Assistant
Updated only the two requested areas.

Root cause:
- Group-aware UI code was classifying channels by display names, but saved `channel_groups` are keyed by raw channel names. In bipolar mode, display names are montage pair names, so lookups fell back to `"macro"` and the viewer/PSD/computation UI lost the intended grouping.
- In `menus.py`, the top-level `view_menu` variable was later reused for the Review submenu `"Event Viewers"`, so `_menus_disabled_until_loaded` tracked the wrong menu object.

Exact fix:
- In [plot.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/plot.py#L591), `get_channel_group()` now keeps the existing direct lookup, and when the viewer is in bipolar mode it resolves a pair display name back to its source channels. It returns `"micro"` only when both source channels are micro; otherwise it preserves the previous fallback of `"macro"`.
- In [main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L1145), `_sync_comp_panel_context()` now passes display-name keyed groups derived from `self.viewer.get_channel_group(...)` instead of the raw `self.channel_groups` mapping.
- In [main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L1248), `_refresh_psd_panel_context()` now uses `self.viewer.get_channel_group(...)` for the same reason.
- In [menus.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/menus.py#L76), I renamed the Review submenu variable from `view_menu` to `event_viewers_menu`, so `_menus_disabled_until_loaded` continues to point at the actual top-level View menu.

Assumption:
- For a bipolar pair whose two source channels do not both resolve to `"micro"`, I treated the display channel as `"macro"` to preserve the prior default/fallback behavior.

I verified the patch with a local diff; I did not run the GUI.

## User

Make these PSD fixes only, with minimal changes:

1. Hide the PSD tab unless the user explicitly opens it.
2. Add a way to close the PSD tab.
3. When re-referencing changes, automatically refresh/update the PSD if it is open.
4. In bipolar mode, do not send all channels to the macro PSD display. Preserve correct grouping behavior for displayed channels. If mixed pairing, channel 1 group is choose 

First explain the root cause briefly.
Then implement the fixes.
Then summarize exactly what changed.
Do not make unrelated changes.

## Assistant
Root cause:
- The PSD tab was always added and left visible in the main tab widget, with no close path.
- PSD grouping was derived from `channel_groups` using display names; in bipolar mode those display names are pair names, not raw channel names, so the lookup defaulted to macro.
- Re-referencing updated viewer display names but did not explicitly refresh PSD when the PSD tab was open.

Implemented fixes:
- In [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L194), enabled tab close handling on `main_tabs`, hid the Viewer close button, stored the PSD tab index, and hid the PSD tab by default with `_set_psd_tab_visible(False)`.
- In [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L1450), added `_on_main_tab_close_requested()` so closing the PSD tab just hides it and returns focus to the Viewer.
- In [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L418), hid the PSD tab again when a project is closed so it stays hidden until explicitly reopened.
- In [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L1440), `_refresh_display_name_dependent_ui()` now refreshes PSD too, and [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L1242) only performs that refresh when the PSD tab is actually open.
- In [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L1454), added `_split_display_channels_for_psd()` and used it from both PSD-open and PSD-refresh flows. In bipolar mode it resolves each displayed pair back to its montage pair and uses `ch1`’s group when classifying that PSD trace; otherwise it keeps the existing raw-name grouping behavior.

Exactly what changed:
- Added PSD tab show/hide helpers and tab-close handling.
- Hid the PSD tab on startup and after project close.
- Reused the existing PSD refresh path when display names change from re-referencing.
- Replaced PSD channel grouping logic with a PSD-specific helper so bipolar displayed channels are classified correctly, using channel 1 for mixed pairs.

Assumption:
- I interpreted “If mixed pairing, channel 1 group is choose” as “use channel 1’s group for PSD classification when a bipolar pair mixes macro and micro channels.”

I verified the patch with a local diff; I did not run the GUI.
