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
- internal zero-phase 4th-order Butterworth bandpass
- default analysis frequency range: 60-140 Hz
- analysis frequency range editable from **Advanced parameters...**
- active notch setting used when enabled
- no automatic common-average reference

REI shows a bipolar montage recommendation before running. You can switch to Bipolar, run anyway, or cancel.

If no notch filter is selected before running, the software warns you so you can confirm whether to continue.

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

### 8.4 HFO Mode

HFO mode is designed for high-frequency oscillation candidate detection,
classification, review, and export on the selected channels.

The HFO time section contains:

- analysis start
- analysis end

By default, the HFO analysis window is set to the full recording.

The HFO input boundary is:

`GUI/file layer -> prepared microvolt signal array -> HFO backend`

The GUI and file layer handle:

- file loading
- channel selection
- user-defined interval selection
- montage and reference selection, including bipolar derivations
- conversion to microvolts

The HFO backend then handles:

- bad-channel exclusion
- the notch mode selected in the GUI
- route-specific preprocessing and sampling handling
- candidate detection
- waveform extraction
- classification

The notch filter is applied once by the HFO backend using the mode selected in
the GUI. The GUI display filter controls the visible traces; the HFO backend
uses the selected notch setting for computation.

Sampling requirements depend on the selected HFO classifier route:

- `pyhfo_pybrain` preserves native EDF sampling, matching the pyBrain GUI route
- `pyhfo_omni_legacy` rejects recordings below 1000 Hz and resamples higher-rate
  recordings internally to 1000 Hz
- `eHFO` uses the Omni-compatible route: recordings below 1000 Hz are rejected,
  and higher-rate recordings are resampled internally to 1000 Hz

The default validated user-facing HFO classifier is **pyhfo_pybrain**.
It uses:

- candidate detectors: STE, MNI, and Hilbert
- pyBrain-compatible default band: 80-500 Hz
- 2-second waveform extraction around each candidate
- original pyHFO/pyBrain Model A for real-HFO acceptance
- original pyHFO/pyBrain Model S for spike-HFO classification after Model A accepts
  the event

The HFO advanced-parameter dialog lets you:

- enable or disable candidate detectors
- edit detector parameters
- restore default detector parameters
- save the edited defaults for later GUI sessions

At least one candidate detector must remain selected. Detector-specific
parameter fields are disabled when their detector is disabled. Advanced changes
are applied only after clicking **Save** in the dialog.

The pyBrain 80-500 Hz band is the default for `pyhfo_pybrain`. Selecting
`pyhfo_omni_legacy` or `eHFO` switches the band preset to the validated
Omni-compatible 80-300 Hz route. Ripple, fast-ripple, and custom band options
are disabled until those configurations are validated separately.

Boundary handling:

- candidates longer than the maximum duration are excluded before classification
- candidates inside the configured boundary padding from the analysis-window
  start or end are excluded before classification
- boundary and exclusion counts are stored in metadata

During the run, HFO analysis runs in the background. The bottom-left status area
shows the current step, elapsed time, and remaining time when possible. A cancel
button is available during processing. When the run finishes, the status area
keeps a summary with the total events and run duration until another computation
starts or the computation panel is closed.

HFO outputs inside the app:

- channel summary window
- event grid with card review
- event filters by channel, class, and order
- main-viewer HFO markers
- zoom review with raw trace, detector-band filtered trace, spectrogram, event
  metadata, classifier proposition, and manual class selector

HFO classes are handled as separate fields:

- `final_model_class`: immutable classifier proposition
- `manual_class`: user-reviewed class, if changed
- `official_class`: active class used for display, summaries, and active counts
- `manual_review_status`: `unreviewed`, `reviewed`, or `deleted`

If the user manually changes an event class, the classifier proposition remains
visible and unchanged. The manual class becomes the official class. If the user
deletes an event, it is excluded from active counts but kept in the exported
event table.

The implemented HFO algorithms are derived from these source repositories:

- Omni-iEEG: https://github.com/Omni-iEEG/Omni-iEEG/tree/master/omni_ieeg
- pyHFO pyBrain branch: https://github.com/roychowdhuryresearch/pyHFO/tree/pyBrain
- pyHFO repository: https://github.com/roychowdhuryresearch/pyHFO

Three HFO classifier routes are available. The default user-facing option is
`pyhfo_pybrain`.

| GUI classifier option | Internal implementation | Preprocessing expectation | Reference target | Validation result | Notes |
| --- | --- | --- | --- | --- | --- |
| `pyhfo_pybrain` | pyBrain-compatible candidate-pool inference | Native EDF sampling in the original pyBrain GUI; detector/filter default is 80-500 Hz; classifier features commonly use 10-500 Hz before checkpoint crop | Original pyHFO/pyBrain GUI classifier from https://github.com/roychowdhuryresearch/pyHFO/tree/pyBrain | Classifier-pool validation: Zurich15 53 / 53 labels matched; HUP134 500 / 500 labels matched. Full-pipeline validation on Zurich15: STE 1 / 1 events and labels, MNI 36 / 36 events and labels, Hilbert 43 / 43 events and labels. | Default user-facing option. |
| `pyhfo_omni_legacy` | Omni-style batch waveform inference | Internal 1000 Hz processing; validated 80-300 Hz detector band; 10-500 Hz classifier feature range | Original Omni legacy pyHFO inference code from https://github.com/Omni-iEEG/Omni-iEEG/tree/master/omni_ieeg | Zurich10: 611 / 611 labels matched; keep, artifact, spike, and HFO score max differences were 0.0 | Separate Omni-compatible analysis route. |
| `eHFO` | Omni eHFO three-model inference | Internal 1000 Hz processing; validated 80-300 Hz detector band; 2-second waveform features for artifact, spike, and eHFO neural networks | Original Omni eHFO event-model code from https://github.com/Omni-iEEG/Omni-iEEG/tree/master/omni_ieeg | Zurich10 candidate-pool validation: 611 / 611 labels matched; feature, artifact-score, spike-score, and eHFO-score max differences were 0.0 | Selectable Omni eHFO option; not the default. |

Each route therefore reached 100% agreement with its own direct reference
implementation in the tested candidate pools. They should not be described as a
single identical classifier path, because they reproduce different upstream
execution flows.

Implementation note: the codebase separates preprocessing modules under
`app/computation/hfo/preprocessing/`. The `pyhfo_pybrain` pipeline uses
`preprocessing/pybrain.py`, preserves native sampling, applies the pyBrain
Chebyshev-II HFO bandpass before candidate detection, and sends the native
non-bandpassed signal plus candidate coordinates to the pyBrain-style
classifier. The `pyhfo_omni_legacy` and `eHFO` pipelines use
`preprocessing/omni.py` and resample to 1000 Hz.

HFO export creates:

- `hfo_channel_summary.csv`
- `hfo_events.csv`
- `hfo_metadata.json`
- `README.txt`

`hfo_channel_summary.csv` contains per-channel candidate counts, accepted HFO
counts, HFO counts, spike-HFO counts, eHFO counts, spike-eHFO counts, artifact
counts, rates per minute, deleted event counts, and boundary event counts.

`hfo_events.csv` contains one row per retained event with channel, timing,
candidate detector, boundary warning, probabilities, classifier proposition,
manual class, official class, review status, selected band, and sampling
details.

`hfo_metadata.json` records the analyzed file, analysis window, selected
channels, bad-channel exclusion, montage/reference information, notch setting,
candidate detectors, detector parameters, sampling rates, processing order,
classifier status, output counts, selected algorithm route, algorithm origin,
classifier origin, checkpoint family, class mapping, and source GitHub
repositories.

HFO import restores exported HFO result folders produced by the application. The
import validates that the result belongs to the same recording file, then
restores events, manual review fields, classifier proposition, probabilities,
metadata, filters where possible, and main-viewer markers.

The `eHFO` route uses the Omni eHFO deep-learning classifier implementation
with official checkpoints:

- `artifacts.pth`
- `spikes.pth`
- `eHFOs.pth`

This classifier-only path has been validated against the official Omni source on
the Zurich10 candidate pool with exact feature, score, and label agreement. It
is selectable from the HFO classifier menu, but it is not the default
user-facing HFO classifier option.

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
