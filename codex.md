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
## User
in the different rereferencing, what is the rule for label naming ? especially in the bipolar

## User
in our dataset, what participant has expert annotation and at least one pathological spike

## Assistant
For label naming: monopolar, average, median, and common reference keep the original imported channel labels. Only the waveform values change. Bipolar replaces display labels with pair names. Automatic bipolar parsing normalizes labels by removing a leading EEG prefix, stripping trailing reference suffixes like -G2/G2, removing spaces, then extracting a core contact label such as RAI1. Consecutive contacts within the same electrode prefix become labels like RAI1-RAI2. Edited/manual bipolar rows use the same core-label rule, so changing Channel 2 produces Ch1Core-Ch2Core.

For the dataset question: I could not determine the participant from the current workspace. Inside c:\Users\F15\Desktop\Eva\EpilepsyTools\I_EEG, there are no dataset manifest or expert annotation CSV/JSON files; the only matching local data files I found were .vscode JSON settings. The app expects expert annotations to have spike and artifact fields; a pathological spike would correspond to a row whose review label is spike, meaning spike is truthy and artifact is not taking priority. To identify the participant, I need the expert annotation manifest/CSV/JSON available inside this workspace or the dataset path you want me to inspect.## User
over the database, how many event, and of which type each do you identify

## Assistant
From what is available in this workspace, I cannot compute database event counts yet: I found no event database/source files (`*.db`, `*.sqlite`, `*.sqlite3`, `*.csv`, `*.tsv`, `*.json`, `*.xlsx`, `*.parquet`) under `I_EEG`, so there are no event rows here to count.

What the code can identify is:
- Expert event grid review labels: `artifact`, `spike`, `non-spike` (`artifact` has priority over `spike` if both flags are true).
- App annotation kinds: `Spike`, `Ripple`, `Fast ripple`, `Artifact`, `Bad segment`, `Other`.

So: actual counted events are not available from the current workspace; event categories are 3 expert-review labels and 6 app annotation kinds.
## User
knowing that : - Total events: **36,177**
    - Spike-associated HFO: **19,178**
    - Non-spike HFO: **7,711**
    - Artifacts: **9,288**

and - **Accepted HFO (artifact = 1)**
- **Rejected artifact (artifact = 0)**, is it coherent with our expert viewer  

Among accepted HFOs:

- **spkHFO**: spike-associated HFO (`artifact == 1` and `spike == 1`)
- **non-spkHFO**: non-spike HFO (`artifact == 1` and `spike == 0`)

## Assistant
No, not coherent with the current expert viewer.

Your dataset convention says `artifact == 1` means accepted HFO, and `artifact == 0` means rejected artifact. But the viewer currently treats `artifact == 1` as the event label `artifact`, with priority over `spike`.

So today the viewer would classify:
- `artifact=1, spike=1` as `ARTIFACT`, but it should be `spkHFO` / spike-associated HFO.
- `artifact=1, spike=0` as `ARTIFACT`, but it should be `non-spkHFO`.
- `artifact=0, spike=0` as `NON-SPIKE`, but it should be rejected artifact.

The counts themselves are coherent: `19,178 + 7,711 + 9,288 = 36,177`. The issue is only the flag interpretation in the viewer. The viewer logic should effectively be:

```python
if artifact == 0:
    rejected artifact
elif spike == 1:
    spkHFO
else:
    non-spkHFO
```
## User
okey change the code the viewver logic to match what we just say 

## Assistant
Done in `app/expert_event_grid.py`.

The expert viewer now maps the flags as:

- `artifact == 0` -> `Rejected artifact` in red
- `artifact == 1 and spike == 1` -> `spkHFO` in green
- `artifact == 1 and spike == 0` -> `non-spkHFO` in blue

I also updated the legend and removed forced uppercasing so `spkHFO` / `non-spkHFO` display with the dataset naming. Static syntax parsing passed and `git diff --check` passed; I could not run a real import-based behavior check because this environment is missing `pyqtgraph`.
## User
can you explore th ecsv file of omni dataset complement and count how many events, how many artifact ...

## Assistant
I checked inside the current `I_EEG` workspace, and there are no CSV/JSON event files available there to count. The code does reference an external location, `Documents\omni dataset complement`, but the project instructions in `agent_instructions.md` restrict me to this directory and its subdirectories, so I did not inspect that external folder.

If you put the Omni complement CSV/manifest under `I_EEG`, or explicitly give me permission to inspect that external folder despite the local restriction, I can count it with the corrected convention:

- Total events = all rows
- Rejected artifacts = `artifact == 0`
- spkHFO = `artifact == 1 and spike == 1`
- non-spkHFO = `artifact == 1 and spike == 0`
## User
are channel name relabeled after re referencing

## Assistant
Only for bipolar.

In the current code, Monopolar, Average, Median, and Common reference keep the original channel labels. The waveform values change, but `_display_names` is reset to the original `_channel_names`; Common Reference does not append the chosen reference name to each label.

Bipolar is the exception: the displayed labels become the bipolar pair names. For automatic bipolar montage, labels are built from normalized adjacent contacts, for example `EEG RAI1-G2` and `EEG RAI2-G2` become `RAI1-RAI2`. The normalization removes the leading `EEG`, trailing reference suffixes like `-G2`/`G2`, spaces, and leading zeroes in contact numbers. In the manual bipolar editor, changing Channel 2 also regenerates the pair label as `channel1_core-channel2_core`.

So: the raw channel names are not renamed, but the displayed names are replaced by pair names in bipolar mode only.
## User
if the monopolar display is already by default bipalar, what hapen

## Assistant
If the EDF/data is already bipolar by default, then the app's Monopolar mode will simply show those existing bipolar signals as raw channels. In other words, Monopolar means "no extra rereferencing inside the app", not guaranteed true monopolar physiology.

If you then click Bipolar again, the app will try to build a new bipolar montage from those already-bipolar channels and subtract one displayed channel from another. That can create wrong second-order derivations. For example, if raw channels are already `A1-A2` and `A2-A3`, applying Bipolar could produce `(A1-A2) - (A2-A3) = A1 - 2*A2 + A3`, which is not a normal bipolar channel.

So for files that are already bipolar, stay in Monopolar/raw display unless we add a check or metadata flag saying "this recording is already bipolar" and disable/adjust the Bipolar rereference option.
## User
are you sure the channel name display is updated, i checked and the old name is still there 

## Assistant
You are right to question it. The main plot does update from `_display_names` in bipolar mode, but there is an important catch: if the original file already has bipolar-looking channel names, the automatic bipolar pair label can be identical to the old raw label.

In code, `set_bipolar_mode()` sets `_display_names = [pair.name for pair in self._bipolar_pairs]`, and `_draw_labels()` reads `get_channel_names()`, so the main plot label path is connected to the bipolar names.

But the bipolar montage parser extracts the first contact from each raw label. If raw channels are already named like:

```text
RAI1-RAI2
RAI2-RAI3
```

then automatic bipolar pairs become:

```text
RAI1-RAI2
```

for `(RAI1-RAI2) - (RAI2-RAI3)`. That displayed label is the same as the old first channel name, even though the signal has actually been rereferenced again. So in an already-bipolar file, the unchanged-looking label is expected with the current naming rule, and it is misleading.

Also, some UI surfaces intentionally still show raw names, like the channel group editor and common-reference channel picker. The main plot should show bipolar display names, but already-bipolar input can make those names look unchanged.

I would treat this as a UX/logic issue: for already-bipolar recordings, we should either disable Bipolar rereferencing or label second-order derivations explicitly, for example `(RAI1-RAI2)-(RAI2-RAI3)`.
## User
yes i think we should 

## Assistant
Done. I added a guard so Bipolar rereferencing is blocked when the raw channel labels already look like bipolar derivations.

Changed:

- `app/referencing.py`: added `looks_like_bipolar_derivation_label()` to detect labels like `RAI1-RAI2`, while still allowing acquisition-style labels like `EEG RAI1-G2`.
- `app/main_window.py`: `on_reference_bipolar()` now checks raw channel names before building/applying a bipolar montage. If already-bipolar labels are found, it shows a warning and does not apply Bipolar rereferencing.
- `README.md` and `app/docs/user_guide.md`: documented that already-bipolar recordings are blocked from Bipolar rereferencing.

Verification:

- Detection check passed: `RAI1-RAI2` -> blocked, `EEG RAI1-G2` -> allowed.
- Python syntax parse passed for `app/referencing.py` and `app/main_window.py`.
- `git diff --check` passed.
## User
oh so you are blocking the user from redoing it ? we should give the option to still do it though

## Assistant
Yes, agreed. I changed it from a hard block to a warning with an explicit **Apply anyway** option.

Now, when raw labels already look bipolar, the app shows a warning listing example labels and explains that applying Bipolar may create second-order derivations. The default is **Cancel**, but the user can choose **Apply anyway** and the montage will still be applied.

Updated:

- `app/main_window.py`: warning dialog with **Apply anyway** / **Cancel**.
- `README.md`: wording changed to “Warn before…” instead of block.
- `app/docs/user_guide.md`: documents the confirmation behavior.

Verification passed: Python syntax parse and `git diff --check`.
## User
the montage is applied but channel names are not updated ?

## Assistant
Yes, that was the remaining bug. The montage was being applied, but for already-bipolar raw labels the generated pair name could still equal the old first channel name.

I fixed the naming rule so:

- Normal acquisition-style channels still display compact bipolar names, for example `EEG RAI1-G2` + `EEG RAI2-G2` -> `RAI1-RAI2`.
- Already-bipolar-looking channels now display explicit second-order names, for example `RAI1-RAI2` + `RAI2-RAI3` -> `(RAI1-RAI2)-(RAI2-RAI3)`.

Changed:

- `app/referencing.py`: added centralized bipolar display-name generation and regenerated montage names from source channels.
- `app/main_window.py`: refreshes pair names before applying a montage, including saved/edited montages.
- `app/docs/user_guide.md`: documents the explicit labels when applying Bipolar anyway.

Verification passed: behavior check for normal and already-bipolar labels, Python syntax parse, and `git diff --check`.
## User
in the computation pannel  what is the button ‘match main display scaling ‘?

## Assistant
It is a checkbox that makes the computation panel plot use the same amplitude gain as the main viewer.

When it is checked, the computation panel takes the computed signal, currently the mean across selected channels, converts it from volts to microvolts, then applies the same visual gain factor as the main display. So if you change the main viewer amplitude control, the computation panel curve visually scales in the same direction.

When it is unchecked, the computation panel shows the computed signal in raw volts, with the Y axis labeled `Mean voltage (V)`.

It does not change the data, selected channels, reference mode, or filtering. It only changes the plot scaling. A clearer label might be `Use main amplitude gain` or `Scale like main viewer`.
## User
I d like to add this feature : when closing without saving, warning msg

## Assistant
Implemented. When there are unsaved review changes, the app now prompts before closing the project, quitting the app, or replacing the current session with New/Open.

The dialog offers:

- **Save**: saves first, then continues. If Save As is canceled or saving fails, closing is canceled.
- **Don't Save**: continues and discards unsaved review changes.
- **Cancel**: keeps the current project open.

Updated `app/main_window.py`, plus README and the user guide. Verification passed with Python syntax parsing and `git diff --check`.
## User
update the read me and user guide with the new updates if required

by default when i open a new file i want the permanent filter button to be hidden 

## Assistant
Done. Permanent filter controls now stay hidden by default when a file or project is opened; users can still reveal them through **Preprocessing > Permanent Filters**.

Updated:

- `app/main_window.py`: `_open_raw_file()` and `_enable_loaded_ui()` now hide the permanent filter controls instead of showing them after load.
- `app/docs/user_guide.md`: documents that permanent filter controls are hidden by default and toggled from the Preprocessing menu.
- `README.md`: mentions permanent filters as a collapsible control strip.

Verification passed: `app/main_window.py` syntax parse, visibility-call search, and `git diff --check`.
## User
there is some bug with tthe selection rectangle, they somtimes don t appear 

## Assistant
Fixed the likely causes in `app/plot.py`.

I changed the selection previews so they are more robust across annotation, zoom, and scalogram modes:

- Preview rectangles now reattach themselves if a redraw clears them from the plot while the mode is still active.
- Annotation selection preview is drawn above the traces instead of behind them.
- Annotation mode now shows a preview immediately on mouse-down, not only after the first mouse move.
- Zoom selection keeps updating after the drag starts, even if the cursor leaves the signal area before release.

Verification passed: Python syntax parse and `git diff --check`. I did not run an interactive GUI test in this environment.
## User
in the edit bipolar, remove the filter button and instead make each title (pair, and origin) clickable for order filter

## Assistant
Done. In the Edit Bipolar dialog, I removed the old Filter label/combo and made the table headers handle ordering instead.

Now:

- Click **Pair** to sort rows by bipolar pair label.
- Click **Origin** to group/sort manual and automatic rows.
- Click the same header again to reverse the order.
- Added rows and reset-to-default rows keep the current header ordering when one is active.

I also updated the user guide section for editing bipolar montage. Verification passed with Python syntax parsing and `git diff --check`.
## User
comput pannel is hard to get bigger, i want to be able to change the size of my docks more fluently

## Assistant
Done. I made the dock layout more flexible and easier to resize.

Changes:

- `app/main_window.py`
  - Enabled nested/tabbed/animated dock behavior.
  - Gave Computation and Annotations docks movable, floatable, closable behavior.
  - Added lower minimum sizes and expanding size policies.
  - Added a helper that gives docks a useful size when opened.
  - Computation panel now opens wider by default; annotation dock also opens at a more usable size.

- `app/computation_panel.py`
  - Added expanding size policies and smaller minimums for the panel, channel list, and plot, so the dock splitter can resize the panel more smoothly.

- `README.md` and `app/docs/user_guide.md`
  - Updated docs to mention resizable/movable/floating dock panels.

Verification passed: Python syntax parse and `git diff --check`. I did not run an interactive GUI resize test here.
## User
Goal: improve the expert event grid visualization for reviewing candidate HFO annotations.

Please inspect `app/expert_event_grid.py` first, then implement the following UX improvements with minimal, clean changes.

Context:
The expert grid is used to review candidate HFO events. The current display is too focused on the isolated event and uses annotation colors too strongly. We want more temporal context, better contrast, and a clearer clinician-facing review workflow.

Requested changes:

1. Add temporal context around each event
- Instead of displaying only the event interval, display a slightly wider window around each candidate event.
- Default context should be ±2 ms around the event:
  - context_start = max(0, event.start - 0.002)
  - context_end = event.end + 0.002
- Make this configurable as a constant or parameter, e.g. `CONTEXT_MS = 2`.

2. Mark the original event interval clearly
- The event itself should be marked within the larger context window.
- Add a light gray semi-transparent shaded region from `event.start` to `event.end`.
- Add vertical markers at event onset and event offset.
- These markers should appear in the zoomed event view and, if feasible, in the grid preview cells as well.

3. Improve visual contrast
- Do not color the waveform by event/review type.
- Keep waveform and metadata neutral:
  - dark mode: waveform/text should remain white or light neutral
  - light mode: waveform/text should be black or dark neutral
- Use event/review type color only for:
  - the cell/card border
  - a small label/badge
  - optionally the event interval frame
- The annotation color should guide the eye but not interfere with waveform interpretation.

4. Improve click/zoom behavior
- When clicking an event cell, keep the existing behavior that opens the zoomed event view and emits the jump-to-time signal.
- In the zoomed view, show the context window around the event, not just the event duration.
- If the app already has access to the raw channel signal, preserve or improve the behavior so that clicking an event allows inspection of the raw signal of the full channel around the event.
- Ideally, structure the zoomed view as:
  - main waveform: event ±2 ms with shaded event region
  - if easy and already supported: wider channel context view, e.g. ±0.5 s or ±1 s, with the event highlighted
- Do not add major architecture unless necessary.

5. Preserve existing functionality
- Keep pagination/grid behavior unchanged.
- Keep event metadata display unchanged except for visual styling.
- Keep existing signals such as `requestJumpToTime(event.start, event.channel)`.
- Do not break current callbacks used to retrieve waveform data.
- If waveform callbacks currently only accept start/end times, adapt them carefully so they request the wider context range while still highlighting the original event interval.

6. Add clean helper methods if useful
For example:
- `get_event_context_window(event, context_ms=2)`
- `add_event_region(plot_widget, start, end)`
- `get_neutral_waveform_color()`
- `get_review_border_color(review_label)`

7. Code quality
- Keep the implementation simple and readable.
- Add short comments explaining why the event region is gray and why class color is only used on borders/badges.
- Avoid large refactors.
- Run a quick syntax check after changes.

Expected result:
The expert grid should support contextual HFO candidate review:
- waveform shown with ±2 ms context
- event interval marked by gray region and onset/offset lines
- signal remains visually neutral
- review type appears mainly through borders/badges
- clicking an event still opens the zoomed viewer and jumps to the event/channel

After implementing, summarize:
- which file(s) changed
- what visual behavior changed
- any assumptions made about waveform callbacks or available raw signal data

## Assistant
Implemented in `app/expert_event_grid.py`.

What changed:

- Added `CONTEXT_MS = 2` and helper functions for event context windows, neutral waveform color, waveform time axes, and gray event-region overlays.
- Grid preview cells now request/display the event with ±2 ms context when raw waveform callback data is available.
- The original event interval is marked in both grid cells and zoomed view with a semi-transparent gray region plus onset/offset vertical markers.
- Waveforms are now neutral/light instead of review-colored.
- Review type color is used on the cell border and review badge, not the waveform.
- Zoomed view now shows:
  - main event waveform with ±2 ms context
  - a wider channel context plot using ±0.5 s when the raw callback can provide it
- Existing pagination and click behavior are preserved, including `requestJumpToTime(event.start, event.channel)`.

Assumptions:

- The existing waveform callback still accepts `(channel_name, start_time, end_time)` and returns waveform samples only, not timestamps.
- When raw data is available, the grid asks that same callback for the wider time windows. If only CSV-stored waveform snippets exist, the code falls back to the stored event waveform without additional context.
- The wider channel context uses the app’s current raw/active signal source through the existing callback.

Verification passed: `app/expert_event_grid.py` syntax parse and `git diff --check`. I did not run an interactive GUI review here.
## User
what are all the pb in expert event

## Assistant
I inspected the expert event grid and the waveform callback path. I did not change anything.

Main problems I see:
- The zoomed view says "click to return to grid", but ZoomedEventView has no mouse click handler. Only Back, Esc/Return, n, and b work.
- Cell clicking may be unreliable when the click lands on the embedded pyqtgraph widget, because the parent cell owns the click signal but the plot is a child widget that may consume mouse events.
- The "raw channel context" is only as raw as MainWindow.current_raw. If current_raw has been filtered/altered, the expert grid is showing that active raw object, not necessarily the untouched EDF signal.
- Channel matching is exact. If expert CSV labels do not exactly match current_raw.ch_names, or the main viewer is showing relabeled/bipolar channel names, waveform extraction and channel jump can silently fail.
- The main viewer jump uses event.start directly, so the event lands at the left edge of the display instead of being centered with context.
- The jump-to-channel path scrolls near the channel but does not actually select or visually mark the channel.
- Waveform fetching is not cached well. _update_grid calls _ensure_event_waveform for every visible event, and _show_zoomed_view refetches again. Paging back and forth can repeatedly slice raw data.
- The time axis is inferred with linspace(start,end,n). Because the callback returns only samples, not actual sample times, the last point is slightly stretched to the requested end; near file boundaries it can be more wrong because the callback clamps sample indices but the grid still stores the requested end time.
- If the raw callback fails and only CSV waveform snippets exist, there is no real +/-2 ms context. The stored waveform is treated as event-only, so the gray event region covers essentially the whole waveform.
- Waveform callback failures are silent. The UI shows a flat placeholder, which can be mistaken for a flat signal instead of "waveform unavailable".
- The wider context plot is useful, but unlabeled beyond "Channel context" and has no y-axis/unit label, so the two plots can be a little ambiguous clinically.
- Visual styling is mostly improved, but review color still appears as a light badge background, not strictly only border/badge outline. Also the legend still uses colored text rather than the same border/badge language.
- The code is hard-coded for dark mode: plot backgrounds and neutral waveform color always assume dark mode.
- If an events file has no rows matching the EDF name, the loader falls back to showing all events from the file. Useful for some files, but risky because a typo/mismatch can show events from the wrong recording.
- Minor cleanup: several old imports appear unused after the grid changes.

Biggest ones to fix first would be exact channel-name matching, silent waveform failure, no caching, inaccurate sample-time mapping, and the false "click to return" instruction.## User
fix those issues

## Assistant
Fixed the expert-event issues in app/expert_event_grid.py and app/main_window.py.

What changed:
- Event cells and zoom plots now reliably handle clicks; the zoom view's "click to return" text is true now.
- Missing waveform data now shows "Waveform unavailable" instead of a misleading flat trace.
- Waveform fetching is cached per event, including failed fetches, so paging/zooming does not keep slicing the raw data repeatedly.
- The grid accepts callbacks that return exact sample times, and the main callback now returns them, so event plots are no longer stretched with guessed linspace timing.
- Expert waveform extraction uses source_raw when available, so the wider context is from the original signal rather than the active filtered view.
- Channel lookup now tolerates acquisition labels and bipolar labels; if an event is A-B and both contacts exist, the expert grid displays the derived A-B waveform.
- Clicking an event now centers the main viewer around the event and selects/highlights the matched channel when possible.
- EDF filtering is safer: if an event file has EDF names and none match the loaded file, it no longer falls back to showing all events from the file.
- Review colors are kept to borders/badges/legend outlines; waveform and text stay neutral.
- Zoom plots now label amplitude and time.

Verification: ast parse passed for app/expert_event_grid.py and app/main_window.py, and git diff --check passed. I did not run an interactive GUI session.## User
those pb : Parameter "padding" is already assigned
Parameter "padding" is already assigned
"hideAxis" is not a known attribute of "None"
"setMouseEnabled" is not a known attribute of "None"
Cannot access attribute "setMouseEnabled" for class "PlotItem"
  Attribute "setMouseEnabled" is unknown
Parameter "padding" is already assigned
Parameter "padding" is already assigned
Parameter "padding" is already assigned
"widget" is not a known attribute of "None"

## Assistant
Fixed the static-analysis errors in app/expert_event_grid.py by adding typed wrapper helpers around pyqtgraph dynamic calls, replacing direct setXRange/setYRange calls that used padding=0, casting PlotItem access through Any for hideAxis/setMouseEnabled, and guarding takeAt() before calling widget(). Verified with Python AST parse and git diff --check.## User
actually for the grid i would like a total window of 570 ms with the detected event centered. also when we click on an event and the zoom in window appear, there is a bug rn ( wherever i click it brings me back to the main grid)]

## Assistant
Updated app/expert_event_grid.py so event previews use a fixed 570 ms centered context window around the detected event. The plotted x-range now stays at that centered window even if the fallback waveform is only an event snippet. Kept the secondary zoom context as a wider 1.0 s window. Removed the zoom-view click-to-back behavior so clicks inside the zoomed view no longer return to the grid; navigation back is through Back to Grid, Esc, or Return. Verified with AST parse and git diff --check.## User
what hapene to the bipolar montage if i hide or mark as bad after applying it

## Assistant
Currently, after bipolar mode is applied, hide/bad actions mostly target the displayed bipolar pair labels, not the original source contacts.

If you hide a channel in bipolar mode, the hidden name is the bipolar display name, for example RAI1-RAI2. That pair disappears from the main viewer, but the bipolar montage itself is not rebuilt and the pair still exists inside the montage.

If you mark a channel bad in bipolar mode, the bipolar pair stays visible and is recolored red. The pair is not removed from the montage and its signal is still computed. Bad channels are visible by design in the viewer.

Important edge case: when bad channels change, MainWindow clears _saved_bipolar_montage. So if you had manually edited bipolar pairs, then mark something bad afterward, the active montage stays for the moment, but the saved manual montage is invalidated. If you later leave bipolar mode and apply bipolar again, it may rebuild automatically instead of reusing your manual edit.

Another edge case: if the bad name is a bipolar label like RAI1-RAI2 and you rebuild automatic bipolar later, the parser may interpret that label as source contact RAI1. That means reapplying bipolar could skip pairs involving RAI1, which is probably not the intended behavior.

So the current behavior is: hide = hides displayed bipolar pair; bad = colors displayed bipolar pair red; neither action immediately recomputes the active montage. Reapplying bipolar later can rebuild differently because bad-channel state is used during automatic montage generation.## User
for the expert grid, the gray part can dont need to be infinite

## Assistant
Changed app/expert_event_grid.py so the expert-grid event highlight no longer uses pyqtgraph LinearRegionItem/InfiniteLine. The gray event interval is now a finite BarGraphItem bounded to the waveform amplitude range, with finite onset/offset marker lines over the same y-range. Verified with AST parse and git diff --check.
## User
isnt it a display problem of our ieeg signal

## Assistant
Not purely, based on the current code. The main viewer does apply display-only transforms: the amplitude control changes visual scaling, and each channel is vertically offset so traces can be stacked. You can see that in [app/plot.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/plot.py:462) and [app/plot.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/plot.py:479). The user guide also states that amplitude changes only affect how large the waveform appears on screen, not the data itself in [app/docs/user_guide.md](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/docs/user_guide.md:149).

But the app also has non-display signal transformations before plotting: average, median, common-reference, and bipolar modes all change `data_v` before it is converted to microvolts and drawn, as shown in [app/plot.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/plot.py:717), [app/plot.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/plot.py:749), and [app/plot.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/plot.py:769). So if the issue is just that the waveform looks too big, too flat, or visually crowded, yes, that can be a display problem. If the waveform shape or polarity itself looks wrong, then it is more likely a referencing or preprocessing issue rather than only display.

A quick test is: keep the same time window/channel, switch to monopolar, and vary only the `Amplitude (uV)` control. If only the apparent size changes, that confirms display scaling. If the waveform itself changes when switching reference mode, that is a real signal transformation.

## User
should we implement a white mode of the display.

## Assistant
Yes, I think a white mode is worth implementing, but as an optional theme, not a replacement for the current dark viewer.

The current UI is clearly built around a dark display: the main viewer uses white macro traces and light labels in [app/plot.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/plot.py:194), the scalogram windows explicitly use black backgrounds in [app/scalogram_viewer.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/scalogram_viewer.py:131), and the top controls are styled dark in [app/main_window.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/main_window.py:126). So a white mode is feasible and probably useful for daylight use, screenshots, and users who prefer paper-like contrast, but it is not a one-line toggle because several colors are hardcoded.

I would recommend doing it only if we treat it as a small theming pass: centralize viewer colors into a light/dark palette, make the main trace/label/selection colors theme-aware, and update the scalogram and toolbar styles at the same time. I would not frame it as the fix for the signal issue, though, because white mode changes readability and contrast, not the underlying signal behavior.

## User
yeah let s do that

## Assistant
I implemented a light/dark display theme system for the viewer. There is now a shared palette module in [app/display_theme.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/display_theme.py:1), a new `Theme` selector in the main toolbar in [app/main_window.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/main_window.py:554), and the main signal viewer now updates its background, axes, grid/cursor colors, labels, annotation text, and zoom/scalogram preview outlines through theme-aware values in [app/plot.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/plot.py:397).

I also made scalogram windows follow the same theme, including their plot backgrounds and control styling, in [app/scalogram_viewer.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/scalogram_viewer.py:240). New scalogram windows open with the current theme, and open ones update when the toolbar theme changes in [app/main_window.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/main_window.py:586) and [app/main_window.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/main_window.py:2700).

Verification: I syntax-checked the edited files with `ast.parse`. I could not do a runtime import or UI launch here because `pyqtgraph` is not installed in this environment.

## User
when i open a file the raw signal is not well aligned with the channel labels

## Assistant
I adjusted the viewer so each visible channel is centered on its display row before plotting, which should fix the label/trace misalignment you see right after opening a file. The change is in [app/plot.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/plot.py:488), where `_center_traces_for_display()` subtracts each channel’s per-window median for display only, and [app/plot.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/plot.py:498), where the centered traces are used for drawing.

This is a display-only correction: it does not modify the underlying raw data, saved data, or referencing modes. I syntax-checked the file successfully, but I couldn’t run the full UI here because the environment is missing `pyqtgraph`.

## User
what are the two vertical red lines i m seeing

## Assistant
The most likely explanation is that they are interval-boundary markers: one line for the start time and one for the end time of an event/selection. In the expert event waveform view, the app explicitly draws two vertical markers at `start_s` and `end_s` in [app/expert_event_grid.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/expert_event_grid.py:178), inside `add_event_region(...)` in [app/expert_event_grid.py](c:/Users/F15/Desktop/Eva/EpilepsyTools/I_EEG/app/expert_event_grid.py:156).

Those lines are not part of the raw iEEG signal itself. They are overlay guides showing the selected event window. If you are seeing them in the expert-event waveform panel, that is exactly what they are. If you mean the main viewer instead, send me where you are seeing them and I’ll trace that overlay too, because the main viewer uses a movable cursor line and annotation/selection overlays, but not the same paired event markers.


## User
update the user guide with latest updates

## Assistant
Updated both the Markdown source and generated HTML user guide. The guide now covers the Light/Dark theme and Hide all Bad controls, gamma/HFO event filters and the global timeline, importing exported REI/Gamma/HFO results, current HFO classifier labels and all band presets, HFO marker/review behavior, and historical class normalization during import. I also replaced the classifier comparison table with HTML-compatible structured sections. The generated HTML matches a clean second generation byte-for-byte, and `git diff --check` passes. The changes are not committed or pushed.


## User
let s not make the user guide recharge that often, only when i save it

## Assistant
Changed the preview so Safari no longer reloads every two seconds. The page now performs a lightweight background version check and reloads only after `user_guide.md` is saved and a new HTML build is available. Updated the preview tool, documentation, and generated HTML; tested the local version endpoint and confirmed the periodic HTML refresh tag is absent. Restart the preview server to use the new behavior.


## User
event filter and global timeline should appear later, in the computation section of the user guide

## Assistant
Moved â€œEvent Filters and Global Timelineâ€ from â€œMain Window Basicsâ€ to the Compute section, immediately after â€œImport Previously Exported Resultsâ€ and before the REI/Gamma/HFO mode descriptions. Regenerated the HTML guide and verified the new heading order and clean diff.


## User
is the list of what is restore by application when opening a project up to date in the md file user gudie

## Assistant
No. The list is broadly correct but incomplete. It omits restored macro/micro channel groups and display settings (time range, visible-channel count, and amplitude). â€œComputation panel stateâ€ should be clarified to cover the selected algorithm/channels, REI timing and frequency settings, gamma/HFO analysis intervals, and HFO advanced parameters. Full computation results are not restored; only REI result metadata may be saved, so exported results must be imported. The active reference mode, theme, main-viewer time position, open panels/windows, and event-filter selections are not restored.


## User
update it

## Assistant
Updated the project reopening section to list all restored channel groups, display settings, computation setup, HFO parameters, and REI metadata, plus a separate list of state that is not restored. Clarified that complete computation results must be recovered with Import results, and aligned the File > Save section with the same behavior. Regenerated the HTML guide and verified `git diff --check`.


## User
i did some commit from another computer, can you safely push our updates and pulling the other commit here

## Assistant
Fetched every remote branch and confirmed that GitHub's `main` still matched this checkout at `0741843`; no newer commit from the other computer was available remotely. Validated, committed, rebased, and pushed the local user-guide and preview updates to `origin/main` without force-pushing. The other computer must pull `main` before making its next push.
