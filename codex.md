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
- In [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L1454), added `_split_display_channels_for_psd()` and used it from both PSD-open and PSD-refresh flows. In bipolar mode it resolves each displayed pair back to its montage pair and uses `ch1`â€™s group when classifying that PSD trace; otherwise it keeps the existing raw-name grouping behavior.

Exactly what changed:
- Added PSD tab show/hide helpers and tab-close handling.
- Hid the PSD tab on startup and after project close.
- Reused the existing PSD refresh path when display names change from re-referencing.
- Replaced PSD channel grouping logic with a PSD-specific helper so bipolar displayed channels are classified correctly, using channel 1 for mixed pairs.

Assumption:
- I interpreted â€œIf mixed pairing, channel 1 group is chooseâ€ as â€œuse channel 1â€™s group for PSD classification when a bipolar pair mixes macro and micro channels.â€

I verified the patch with a local diff; I did not run the GUI.

## User
Read agent_instructions.md and follow it strictly.

Fix these two issues only, with minimal changes:

1. Remove or replace remaining debug print() calls in plot.py so the GUI does not spam stdout.
2. Make project file paths portable:
- save both absolute and relative raw file paths when possible
- on open, try absolute path first, then relative path from the project file
- if neither works, prompt the user to locate the raw file manually
- keep backward compatibility with existing project files

First explain the root cause briefly.
Then implement the fixes.
Then summarize exactly what changed.
Do not make unrelated changes.

## Assistant
Root cause:
- `app/plot.py` still contained temporary debug `print()` calls inside render/reference code, so normal GUI use could spam stdout.
- Project files only stored a single raw-file path and the open flow only tried that exact location, which made saved projects brittle when moved between folders or machines.

Implemented fixes:
- In [app/plot.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/plot.py), removed the remaining debug `print()` calls from the average/median reference path and bad-segment mask builder.
- In [app/project_io.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/project_io.py#L51), project saving now stores:
  - `source.raw_file` as the absolute raw path, preserving current behavior
  - `source.raw_file_relative` as a project-relative path when it can be computed
- In [app/project_io.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/project_io.py#L114), project loading now accepts either `raw_file` or `raw_file_relative`, so older project files remain valid.
- In [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L809), project open now:
  - tries `source.raw_file` first
  - then tries `project_path.parent / source.raw_file_relative`
  - if neither exists, prompts the user to locate the raw EEG file manually

Exactly what changed:
- Deleted the remaining stdout debug prints in `plot.py`.
- Added relative raw-path serialization in `project_io.py` while keeping absolute path serialization.
- Relaxed project validation so either absolute or relative raw path is accepted.
- Updated project open in `main_window.py` to resolve raw files in this order: absolute, relative-to-project, manual user selection.

Backward compatibility:
- Existing project files that only contain `source.raw_file` still open the same way.

I verified the patch with a local diff; I did not run the GUI.

## User
Read agent_instructions.md and follow it strictly.

Fix these two issues only, with minimal changes:

1. Fix visible encoding/mojibake issues in UI text and docs (examples: â€¦, ÂµV, â€“) in files such as README.md, app/menus.py, app/plot.py, and app/computation_panel.py.
2. Start splitting MainWindow into a cleaner structure, but keep the change small and safe:
- extract one clearly scoped responsibility from app/main_window.py into a separate module/class
- prefer a low-risk extraction such as project loading/saving, reference handling, or filter handling
- keep behavior unchanged

First explain the root cause briefly.
Then propose the minimal safe refactor.
Then implement it.
Then summarize exactly what changed.
Do not make unrelated changes.

## Assistant
Root cause:
- Several user-facing strings in the UI layer had been saved with inconsistent encoding at some point, leaving visible broken text in menus and labels.
- `MainWindow` was also handling project file dialogs and path-resolution details inline, which is a low-level responsibility that can be split out safely without changing app behavior.

Minimal safe refactor:
- Extract only the project file dialog/path workflow into a helper class, while keeping project state application, save/load behavior, and UI updates in `MainWindow`.

Implemented changes:
- Added [project_file_helpers.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/project_file_helpers.py), which encapsulates:
  - choosing a raw EEG file
  - choosing a project file to open
  - choosing a project file to create/save
  - resolving a raw file path for a project, including the existing absolute/relative/manual-locate behavior
- Updated [main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py) to call `ProjectFileHelper` from `on_new_project()`, `on_open_project()`, and `on_save_project_as()` instead of handling those dialogs and suffix/path details inline.
- Fixed visible broken UI text in:
  - [menus.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/menus.py)
  - [plot.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/plot.py)
  - [computation_panel.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/computation_panel.py)
  - [main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py)

Exactly what changed:
- Normalized broken UI text such as:
  - `Save as…` -> `Save as...`
  - `Hidden…` -> `Hidden...`
  - `Edit Bipolar…` -> `Edit Bipolar...`
  - `Optional note…` -> `Optional note...`
  - broken microvolt/min-max display strings -> ASCII-safe `uV` and `+/-`
  - broken annotation time separator -> `-`
- Kept README unchanged because it did not show a concrete broken string in this pass.
- Moved only the project file chooser/path resolution responsibility out of `MainWindow`; behavior of project creation/open/save remains the same.

I verified the patch with a local diff and a final scan for the broken mojibake patterns in the targeted files; I did not run the GUI.

## User
okey let s redo a full analysis of this code, check for errors and possible improvement

## Assistant
**Findings**
- High: Reopened projects still lose the active reference mode. Project save persists edited bipolar montage data, but not the actual active reference mode, and project open ends by forcing monopolar mode. See [app/project_io.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/project_io.py#L40), [app/project_io.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/project_io.py#L69), and [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L892).
- Medium: `MainWindow` still reaches into `PSDPanel` private state and private methods directly, which makes the tab fragile and tightly couples two widgets that should communicate through a public API. See [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L2329), [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L2330), [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L2333), [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L2374), and [app/main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L2397).
- Medium: The menu bar still contains many placeholder actions wired to `print("TODO")`, so users can trigger dead-end features and the app still writes to stdout from UI actions. See [app/menus.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/menus.py#L32), [app/menus.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/menus.py#L69), and [app/menus.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/menus.py#L106).
- Low: The computation panel exposes an `Algorithm` selector, but only one option exists and `state.algorithm` is never used in the actual computation path, so the UI suggests extensibility that the implementation does not yet support. See [app/computation_panel.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/computation_panel.py#L126), [app/computation_panel.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/computation_panel.py#L150), and [app/computation_panel.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/computation_panel.py#L458).
- Low: The README install section is incomplete; it ends inside a code fence after `pip install -r requirements.txt`, which makes the docs look truncated. See [README.md](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/README.md).

**Architecture**
- Entry point is [main.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/main.py), which launches [MainWindow](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L55).
- The separation into [plot.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/plot.py#L44), [referencing.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/referencing.py), [filtering.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/filtering.py), and [project_io.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/project_io.py) is a good foundation.
- The main structural weakness is that [main_window.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/main_window.py#L55) is still a very large coordinator handling file IO, state restoration, re-referencing, filtering, PSD wiring, annotations, and multiple dialogs in one class.

**Improvements**
- Persist and restore full reference state in project files, not just edited bipolar montage data.
- Give `PSDPanel` a small public sync API like `set_bad_channels(...)` / `refresh_from_context(...)` so `MainWindow` stops mutating underscore-prefixed internals.
- Replace menu `TODO` actions with disabled actions or user-facing “Not implemented yet” dialogs to avoid stdout writes from the GUI.
- Either remove the computation panel algorithm selector for now or implement the algorithm dispatch so the control is real.
- Continue the small-scope extraction approach that already started with [project_file_helpers.py](/c:/Users/m2b3m/Documents/EpilepsyTools/I_EEG/app/project_file_helpers.py): filters or reference handling would be the next safest splits.
- Add a small smoke-test layer around project save/open, re-reference switching, and PSD refresh because those flows are stateful and regress easily.

**Open Questions**
- Should project reopen restore the exact last review view, including reference mode and PSD interval, or is monopolar-on-open intentional?
- Are the many placeholder menu entries part of an active roadmap, or should they be hidden until implemented?

I did not run the GUI or automated tests; this is a static review of the current source tree.

## User
let s update the read me and guide user if required

## Assistant
Updated README.md and app/docs/user_guide.md for the recent user-facing changes.

README now mentions channel-group sorting and PSD plot zoom/double-click reset with bad channels staying visible in red. The user guide now documents editing channel groups and sorting by the Channel header, notes that edited annotation times update the main plot after OK, fixes the PSD bad-channel typo, describes linked bad-channel state between the main viewer and PSD tab, and adds PSD plot zoom/reset instructions.

Verification: git diff --check passes.