# iEEG Tool

A desktop application for reviewing intracranial EEG (iEEG) recordings with interactive visualization, rereferencing tools, annotation support, scalogram review, PSD tools, computation workflows, result export, and project-based review state.

Built with **PySide6**, **PyQtGraph**, **MNE**, NumPy, and SciPy.

## Overview

iEEG Tool supports clinical or research review of multichannel EEG/iEEG data. The main viewer provides a stacked multichannel signal display, interactive navigation, rereferencing options, annotation tools, PSD and computation panels, scalogram windows, and project files that preserve review state across sessions.

The application is built around an MNE-based loading pipeline and a PySide6 / PyQtGraph interface.

The program is structured around a main viewer that:

- loads EEG/iEEG data using MNE
- displays channels stacked vertically
- shows a selected time window of the recording
- supports scrolling, zooming, channel selection, annotation, and scalogram selection

Main components include:

- `main_window.py` - application logic and UI coordination
- `plot.py` - `MultiChannelViewer` rendering and interaction engine
- `scalogram_viewer.py` - selected-channel time-frequency review window
- `menus.py` - menu structure
- `console_viewer.py` - console output window
- `computation/` - REI, gamma spike, HFO, import, and export workflows

Rendering is optimized to:

- load only visible channels
- load only the visible time window
- decimate data for responsiveness

## Main Features

- Load EEG/iEEG recordings from common electrophysiology formats
- Scroll through multichannel data with adjustable time window, channel count, and gain
- Switch between monopolar, bipolar, average, median, and common-reference modes
- Automatically generate bipolar montages from channel labels
- Warn before bipolar rereferencing when raw channel labels already look bipolar
- Edit bipolar pairs manually
- Assign channels to macro/micro groups and sort the channel-group table by label
- Mark channels as hidden or bad
- Add and edit annotations directly in the viewer
- Use transparent outlined selection rectangles for annotation, zoom, and scalogram workflows
- Use zoom selection for rectangular zoom-in review
- Open scalogram windows from a selected channel/time interval and filter displayed frequencies
- Apply windowed display filters from a collapsible control strip
- Inspect PSD plots with mouse zoom and double-click reset; bad channels remain visible in red
- Run computation-panel workflows for channel mean traces, Recruitment Energy Index (REI), gamma spike analysis, and HFO analysis
- Enter seizure onset/offset and baseline/ictal windows manually for REI runs
- Review REI results in a sortable summary table and heatmap
- Review gamma spike results in channel-level and spike-level tables with raw trace, boundary, and time-frequency views
- Run gamma spike analysis through one segmented pipeline for short and long recordings
- Use segmented gamma spike detection with full-channel boundary/gamma measurements while keeping the selected software notch behavior
- Run gamma spike detection in the background with progress, estimated remaining time, and cancellation
- Run HFO candidate detection, pyHFO classification, and Omni eHFO classification from the computation panel
- Review HFO events in a resizable event grid with filters, main-viewer markers, zoom review, classifier proposition, manual class correction, and deletion
- Export REI, gamma spike, and HFO results directly from the computation panel after the algorithm has run
- Export and import HFO results with event-level, channel-level, metadata, and manual review fields
- Export compact metadata JSON files, CSV summaries, and README notes for output folders
- Export REI heatmap values and a saved REI heatmap figure
- Warn before overwriting existing computation output files
- Save and reopen project state
- Warn about unsaved review changes before closing
- Use resizable computation, annotation, and PSD panels for selected channels

## Computation Outputs

Computation exports are available from the computation panel after a result exists.

REI export includes:

- `rei_summary.csv`
- `rei_heatmap.csv`
- `rei_heatmap.png`
- `rei_metadata.json`
- `README.txt`

Gamma spike export includes:

- `gamma_channel_summary.csv`
- `gamma_spike_events.csv`
- `gamma_metadata.json`
- `README.txt`

`gamma_channel_summary.csv` contains one row per channel with total spikes,
gamma-positive spikes, non-gamma spikes, gamma rate, mean gamma power, and mean
gamma duration. `gamma_spike_events.csv` contains one row per retained spike
with spike timing, boundary points, gamma measurements, and any processing
error.

Gamma spike heatmaps are shown inside the review UI but are not saved during export, because saving one heatmap per spike can create very large output folders.

HFO export includes:

- `hfo_channel_summary.csv`
- `hfo_events.csv`
- `hfo_metadata.json`
- `README.txt`

`hfo_channel_summary.csv` contains one row per channel with candidate counts,
accepted HFO counts, HFO counts, spike-HFO counts, eHFO counts, spike-eHFO
counts, artifact counts, rates per minute, deleted event count, and boundary
event count.
`hfo_events.csv` contains one row per retained HFO candidate with timing,
candidate detector, classifier probabilities, immutable classifier proposition,
manual review fields, derived official class, band settings, and sampling
metadata.

## Gamma Spike Pipeline

Gamma spike analysis uses a memory-conscious segmented pipeline:

1. The detector runs in 10-minute chunks with 10 seconds of context.
2. Detector settings are `-bl 10 -bh 60 -h 60 -k1 3.65 -dec 200`.
3. Per-chunk detections are merged and postprocessed once globally.
4. Spike boundary and gamma measurements are computed one channel at a time from full-channel filtered signals.
5. Boundary/gamma filtering keeps the selected software notch behavior, including 60 Hz harmonics when selected.

The run happens in a background worker. While it is running, the bottom-left
status area shows the current processing step, time so far, and estimated time
remaining. When it finishes, the status area keeps a summary with total spikes,
gamma-positive spikes, and total run duration until another computation starts
or the computation panel is closed.

This is the gamma spike pipeline used by the computation panel. The older
Python/export behavior is kept internally for validation work, but it is not
shown as a user-facing option.

## HFO Pipeline

HFO analysis uses an Omni/pyHFO-style backend while keeping the GUI responsible
for file loading, channel selection, interval selection, montage/reference
selection, and microvolt conversion.

The default user-facing HFO path is:

1. The GUI provides a prepared channel x sample signal array in microvolts.
2. Bad channels are excluded.
3. The HFO backend applies the notch mode selected in the GUI once.
4. The selected HFO route applies its own preprocessing:
   `pyhfo_pybrain` preserves native sampling; `pyhfo_omni_legacy` and `eHFO`
   use the Omni-compatible 1000 Hz route.
5. Candidate detection runs with the selected route band: 80-500 Hz for
   `pyhfo_pybrain`, 80-300 Hz for `pyhfo_omni_legacy` and `eHFO`.
6. The backend extracts 2-second waveforms centered on candidate events.
7. The selected classifier runs:
   - Model A accepts real HFO versus artifact.
   - Model S classifies accepted events as non-spike HFO or spike-HFO.
   - For `eHFO`, a third Omni eHFO model also classifies accepted events as
     eHFO-positive or eHFO-negative.
8. Results are shown in the HFO event grid, channel summary, main viewer
   markers, and export files.

The candidate detector choices are STE, MNI, and Hilbert. At least one detector
must remain selected. Advanced detector parameters are editable, can be restored
to defaults, and are applied only after saving the advanced-parameter dialog.
The default user-facing HFO option is `pyhfo_pybrain`, using the pyBrain-native
80-500 Hz detector/filter route. The Omni legacy pyHFO and Omni eHFO options
remain available as separate 80-300 Hz, 1000 Hz resampled paths. Ripple and
fast-ripple presets are reserved for later validation.

HFO review keeps three class concepts separate:

- `final_model_class`: the immutable classifier proposition.
- `manual_class`: the reviewer correction, if any.
- `official_class`: the active class used for display, summaries, and active
  counts. It equals `manual_class` after review, otherwise `final_model_class`.

The implemented HFO algorithms come from these reference repositories:

- Omni-iEEG: https://github.com/Omni-iEEG/Omni-iEEG/tree/master/omni_ieeg
- pyHFO pyBrain branch: https://github.com/roychowdhuryresearch/pyHFO/tree/pyBrain
- pyHFO repository: https://github.com/roychowdhuryresearch/pyHFO

Two pyHFO classifier implementations are available:

- `pyhfo_pybrain`: default pyBrain-compatible path.
- `pyhfo_omni_legacy`: Omni legacy path kept as a separate validated option.

`pyhfo_omni_legacy` follows the Omni-iEEG legacy pyHFO event-model code and has
been validated directly against Omni's legacy pyHFO inference code on Zurich10:

- 611 / 611 labels matched.
- keep, artifact, spike, and HFO score max differences were 0.0.

`pyhfo_pybrain` follows the pyHFO `pyBrain` branch and has been validated
directly against the pyHFO/pyBrain reference GUI classifier on candidate pools:

- Zurich15: 53 / 53 labels matched.
- HUP134: 500 / 500 labels matched.
- Full Zurich15 pyBrain-native GUI route:
  - STE: 1 / 1 events matched; 1 / 1 labels matched.
  - MNI: 36 / 36 events matched; 36 / 36 labels matched.
  - Hilbert: 43 / 43 events matched; 43 / 43 labels matched.

`pyhfo_pybrain` uses its own preprocessing route: native EDF sampling is
preserved, pyBrain's Chebyshev-II HFO bandpass is applied before candidate
detection, and classifier features are reconstructed from the native
non-bandpassed signal plus candidate coordinates. `pyhfo_omni_legacy` remains on
the Omni-compatible 1000 Hz processing route.

Events can be manually reclassified or deleted in the HFO zoom review. Deletion
excludes the event from active counts but preserves the event record for audit.

The HFO backend also contains the Omni eHFO deep-learning classifier path from
the Omni-iEEG event-model code, with the official `artifacts.pth`, `spikes.pth`,
and `eHFOs.pth` checkpoints. The classifier-only implementation has been
validated against the official Omni code on the Zurich10 candidate pool with
exact feature, score, and label agreement. The eHFO option is selectable in the
HFO classifier menu but is not the default.

HFO JSON exports include an `algorithm_details` object and `source_repositories`
list. These record the selected route, preprocessing expectation, detector
origin, classifier origin, checkpoint family, class mapping, and the GitHub
repositories used as implementation references.

## Installation

### Install Python

Recommended versions:

- Python 3.10
- Python 3.11

Download Python from:

https://www.python.org/downloads/

### Install Dependencies

From the project root folder:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Use a machine-local virtual environment. Do not copy `.venv` between Windows
and macOS.

On macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

After installing dependencies, verify the application imports:

```bash
python -m compileall -q app
python -c "import PySide6, mne, pyqtgraph, torch, torchvision, skimage, safetensors; print('dependency check ok')"
```

HFO/pyHFO/eHFO checkpoints are tracked under:

```text
app/computation/hfo/checkpoints/
```

Verify they are present after cloning or copying:

```bash
ls app/computation/hfo/checkpoints/pyhfo_legacy_binary
ls app/computation/hfo/checkpoints/ehfo
```

Expected files:

```text
model_a.tar
model_s.tar
artifacts.pth
spikes.pth
eHFOs.pth
```

## Running

From the project root folder:

```bash
python main.py
```

If you are using the project virtual environment on Windows:

```bash
.\.venv\Scripts\python.exe main.py
```

If you are using the project virtual environment on macOS:

```bash
source .venv/bin/activate
python main.py
```

## User Guide

The full guide is bundled at:

`app/docs/user_guide.md`

It is also available inside the application from **Help > User Guide**.
