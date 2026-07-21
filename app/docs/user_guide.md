# User Guide

## 1. First Start: Create or Open a Project

### 1.1 Create a New Project

Use **File > New Project...** to start a new review.

1. Choose the raw EEG/iEEG recording
2. Choose where to save the `.ieeg` project file
3. Confirm

The project is linked to the selected raw recording. Once loaded, the signal viewer, toolbar, time navigation, montage label, and window title update.

### 1.2 Open an Existing Project

Use **File > Open Project...** to reopen a saved `.ieeg` project.

When a project is reopened, the application restores:

- the linked raw file
- annotations
- hidden channels
- bad channels
- saved edited bipolar montage
- display filter settings
- computation panel state

The active reference mode itself is not automatically restored. If needed, reselect Bipolar, Average, Median, or Common Reference after opening.

### 1.3 Supported File Formats

The current loader supports:

- `.edf`
- `.bdf`
- `.fif`
- `.vhdr`
- `.set`
- `.cnt`

### 1.4 What Loads After Opening

After a recording or project opens:

- the signal viewer shows the traces
- the toolbar becomes active
- the time navigation bar appears
- the current montage/reference label is shown above the viewer
- menus linked to the loaded recording become available

---

## 2. Main Window Basics

### 2.1 Menu Bar

The menu bar groups the main actions:

- **File**: create, open, save, close, and exit projects
- **View**: zoom selection, reset zoom, and scalogram mode
- **Channels**: channel groups, hidden channels, and bad channels
- **Preprocessing**: montage/reference tools, display filters, and power spectrum
- **Compute**: open the computation panel
- **Review**: annotations
- **Help**: open this user guide

### 2.2 Toolbar

The toolbar controls the main display:

- **Time Range (s)**: number of seconds visible at once
- **Channels**: number of channels visible at once
- **Amplitude (uV)**: manual vertical display scaling
- **Hidden...**: restore hidden channels
- **Bad...**: review and unmark bad channels
- **Edit Bipolar...**: edit the bipolar montage when bipolar mode is active

### 2.3 Signal Viewer

The signal viewer displays stacked EEG/iEEG traces over time.

You can select channels, scroll through the recording, inspect annotations, and open context-menu actions from the viewer.

### 2.4 Side Panels

The application may show docked panels for annotations, computations, and PSD review.

Dock panels can be resized by dragging their divider. The Annotations and Computation docks can also be moved to another dock area or floated from their title bars.

---

## 3. Toolbar and Navigation

### 3.1 Time Range

Use **Time Range (s)** in the toolbar to choose how many seconds are visible at once.

You can type a value directly or use the preset arrow menu.

### 3.2 Visible Channels

Use **Channels** in the toolbar to choose how many channels are visible at once.

You can type a value directly or use the preset arrow menu.

### 3.3 Amplitude Scale

Use **Amplitude (uV)** in the toolbar to change the vertical size of the traces.

This changes only the display. It does not change the raw data or computation results.

### 3.4 Move Through Time

Use the time bar below the viewer to move through the recording.

You can also use:

- **Left Arrow** and **Right Arrow** to move backward or forward
- **Shift + Left Arrow** and **Shift + Right Arrow** to move faster
- **Ctrl + Left Arrow** and **Ctrl + Right Arrow** to move much faster

### 3.5 Move Through Channels

To move vertically through channels:

- use the mouse wheel in the signal area
- use **Up Arrow** and **Down Arrow**
- hold **Shift** with the arrow keys to move faster
- hold **Ctrl** with the arrow keys to move much faster

### 3.6 Select Channels

To select one channel, left-click the trace or its label.

To select several channels:

- **Ctrl + click** adds or removes one channel
- **Shift + click** selects a range
- **Ctrl + Shift + click** adds a range to the current selection

Selected channels are highlighted in the viewer.

---

## 4. File Menu

### 4.1 Save

Use **File > Save** to save the current project.

Saved projects preserve review state such as annotations, hidden channels, bad channels, edited bipolar montage state, display filters, selected computation settings, and last REI result metadata when available.

### 4.2 Save As

Use **File > Save As...** if the project does not yet have a save path or if you want to save it as another file.

### 4.3 Close Project

Use **File > Close Project** to close the current project while keeping the application open.

If there are unsaved changes, the application asks whether to save, continue without saving, or cancel.

### 4.4 Exit

Use **File > Exit** to quit the application.

If there are unsaved changes, the application asks what to do before closing.

---

## 5. View Menu

### 5.1 Zoom Selection

Use **View > Zoom Selection** to zoom directly into a time and channel region.

1. Press and hold the **left mouse button**
2. Drag a rectangle over the region you want to inspect
3. Release the mouse button

The viewer zooms into the selected time range and channel range. Press **Escape** to cancel before applying the zoom.

Double left-click in the viewer to go back one zoom step.

### 5.2 Reset Zoom

Use **View > Reset Zoom** to return to the view that was active before the zoom sequence started.

### 5.3 Scalogram Mode

Use **View > Scalogram Mode** to open a time-frequency view from one channel and one selected time interval.

1. Click **View > Scalogram Mode**
2. Drag horizontally on the channel you want to inspect
3. Release the mouse button

The scalogram window shows the selected channel context, raw signal, scalogram image, frequency-range slider, and hover readout. Very short selections are ignored.

Press **Escape** to cancel scalogram mode before opening a window.

---

## 6. Channels Menu

### 6.1 Channel Groups

Use **Channels > Channel Groups...** to set channels as macro or micro.

1. Select one or more channel rows
2. Click **Set selected to Micro** or **Set selected to Macro**
3. Click **OK**

Channel groups control macro/micro styling and group-aware review tools.

### 6.2 Hidden Channels

To hide channels, right-click a selected trace and choose **Hide**.

Use **Channels > Hidden Channels...** or the toolbar **Hidden...** button to see hidden channels and restore them.

Hidden channels disappear from the visible display but are not deleted from the recording.

### 6.3 Bad Channels

To mark channels as bad, right-click a selected trace and choose **Mark as bad**.

If all selected channels are already bad, the context menu offers **Unmark as bad**.

Use **Channels > Bad Channels...** or the toolbar **Bad...** button to see channels currently marked as bad. From that list, you can unmark one channel or unmark all bad channels.

Bad channels are treated as unusable for review-related computations and montage generation.

---

## 7. Preprocessing Menu

### 7.1 Montage / Reference

Use **Preprocessing > Montage / Reference** to switch reference mode.

Available options:

- **Monopolar**: shows each channel as imported
- **Bipolar**: builds an automatic bipolar montage and displays `Channel 1 - Channel 2`
- **Average**: subtracts the shared average from each channel
- **Median**: subtracts the shared median from each channel
- **Common Reference...**: subtracts one chosen physical channel from each displayed channel

Average and Median exclude channels marked as bad and time intervals annotated as "Bad segment".

If the raw channel labels already look bipolar, the Bipolar command asks for confirmation before applying another bipolar derivation.

### 7.2 Edit Bipolar Montage

The toolbar **Edit Bipolar...** button becomes available when bipolar mode is active.

The editor lets you:

- change Channel 2 in automatic pairs
- add manual pairs
- sort rows by pair label or origin
- restore the default automatic montage
- apply edits with **OK** or discard them with **Cancel**

Rules checked by the editor:

- Channel 1 and Channel 2 cannot be the same
- duplicate bipolar names are not allowed
- cross-electrode pairs trigger a warning, but can be kept intentionally
- bad channels cannot be used as Channel 1 in manual rows

### 7.3 Display Filters

Use **Preprocessing > Display Filters...** to show or hide application-level display filters.

Available filters:

- high-pass filter
- low-pass filter
- notch off
- 50 Hz + harmonics notch
- 60 Hz + harmonics notch

Display filters are non-destructive. The original recording is never modified.

Signal flow for display:

`raw data -> reference choice -> display filters -> viewer + PSD + computation panel display`

Validation rules:

- values must be positive
- high-pass must be lower than low-pass
- low-pass must be below Nyquist
- empty input means that filter is off

For large recordings, filters are applied only to the visible time window with padding, then cropped back before plotting.

### 7.4 Power Spectrum

Use **Preprocessing > Power Spectrum** to inspect power spectral density over a chosen interval.

Before opening, the software asks for start and stop times. The interval must stay inside the recording and `stop` must be after `start`.

The PSD panel contains:

- excluded channels on the left
- displayed channels on the right
- one combined PSD plot at the bottom

PSD behavior:

- all channels are displayed by default, including bad channels in red
- hidden channels are shown in the PSD panel
- selected channels have highlighted PSD curves and list labels
- `<<` moves channels to Excluded
- `>>` moves channels to Displayed
- **Exclude all** and **Include all** move all channels at once
- double left-click inside the PSD plot resets the PSD zoom
- **Mark selected as bad** and **Unmark selected as bad** update bad-channel state

---

## 8. Compute Menu

### 8.1 Open Computation Panel

Use **Compute > Open Computation Panel** to open the computation panel.

You can also right-click selected channels in the viewer and choose **Open Computation Panel**.

The panel uses the current dataset, selected channels, and current montage. It can be resized, docked, floated, or moved to another dock area.

Quick selection buttons:

- **All**: select all displayed channels
- **Macro**: select all displayed macro channels
- **Micro**: select all displayed micro channels

### 8.2 REI Mode

REI mode is designed for manual seizure-window entry and delayed execution.

The REI time section contains:

- seizure onset and offset
- baseline start and end
- ictal start and end

Default windows are derived from seizure onset:

- baseline start = seizure onset - 70 s
- baseline end = seizure onset - 10 s
- ictal start = seizure onset - 5 s
- ictal end = seizure onset + 20 s

REI runs only if all timing inputs are coherent and inside the recording when recording duration is available.

The current REI preprocessing uses:

- input data from the current montage
- confirmed bad channels excluded
- display filter ignored
- internal 70-140 Hz zero-phase 4th-order Butterworth bandpass
- no automatic notch filter
- no automatic common-average reference

REI shows a bipolar montage recommendation before running. You can switch to Bipolar, run anyway, or cancel.

REI outputs:

- summary table with channel, REI score, rank, peak HFER activity, and recruitment delay
- heatmap with HFER activity around seizure onset, REI score side bars, sorting controls, and top-N display control
- export files with metadata, CSV outputs, saved heatmap figures, and `README.txt`

### 8.3 Gamma Spike Mode

Gamma spike mode is designed for spike detection and gamma-activity review on selected channels.

The gamma spike time section contains:

- analysis start
- analysis end

By default, the gamma analysis window is set to the full recording.

Gamma spike detection uses a memory-conscious segmented pipeline:

- detector runs in 10-minute chunks with 10 seconds of context
- detector settings are `-bl 10 -bh 60 -h 60 -k1 3.65 -dec 200`
- detections from chunks are merged and postprocessed once globally
- input data comes from the selected channels and current montage
- spike boundary and gamma details are computed one channel at a time from full-channel filtered signals
- boundary/gamma filtering keeps the selected software notch behavior, including 60 Hz harmonics when selected

If no notch filter is selected before running, the software warns you so you can confirm whether to continue.

During the run, the bottom-left status area shows the current processing step, time so far, and estimated time remaining when possible. A **Cancel gamma run** button is shown during processing.

When detection finishes, the status area keeps this summary until another computation starts or the computation panel is closed:

- gamma spike detection completed
- total spikes
- gamma-positive spikes
- total duration of the run

Gamma spike outputs inside the app:

- channel-level summary
- spike grid with raw trace, spike timing, boundary points, gamma window, and time-frequency view
- gamma spike heatmaps for review

Gamma spike export creates:

- `gamma_channel_summary.csv`
- `gamma_spike_events.csv`
- `gamma_metadata.json`
- `README.txt`

Gamma spike heatmaps are not saved during export because saving one heatmap per spike can create very large output folders.

Existing output files are not silently overwritten. If the target folder already contains gamma output files, the software asks for confirmation first.

---

## 9. Review Menu

### 9.1 Annotate

Use **Review > Annotate** to add annotations to the signal display.

1. Choose annotation type
2. Choose annotation scope
3. Add an optional note
4. Click **OK**
5. Drag on the signal display where you want to place the annotation

Annotation scopes:

- clicked channel
- selected channels
- all channels

Press **Escape** to cancel annotation mode before placing an annotation.

To modify an annotation from the viewer, right-click the annotation region and choose **Edit annotation...** or **Delete annotation**.

When annotations exist, the Annotations dock appears. From the list, you can click an annotation to jump to it or delete it from the plot context menu.

---

## 10. Help Menu

### 10.1 User Guide

Use **Help > User Guide** to open this guide from inside the application.

### 10.2 Shortcuts

Use **Help > Shortcuts** to open the keyboard and mouse shortcut list.

---

## 11. Practical Review Workflow

A common workflow is:

1. Create or open a project
2. Inspect the recording in **Monopolar**
3. Mark noisy or unusable channels as **bad**
4. Hide channels if needed for visual clarity
5. Switch reference mode if useful
6. Add annotations
7. Open the computation panel or PSD panel if needed
8. Export results when needed
9. Save the project regularly
