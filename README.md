# iEEG Tool

A desktop application for reviewing intracranial EEG (iEEG) recordings with interactive visualization, rereferencing tools, annotation support, scalogram review, computation/PSD tools, and project-based workflows.

Built with **PySide6**, **PyQtGraph**, **MNE**, NumPy, and SciPy.

## Overview

iEEG Tool supports clinical or research review of multichannel EEG/iEEG data. The main viewer provides a stacked multichannel signal display, interactive navigation, rereferencing options, annotation tools, PSD/computation panels, scalogram windows, and project files that preserve review state across sessions.

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
- Apply permanent filters from a collapsible control strip
- Inspect PSD plots with mouse zoom and double-click reset; bad channels remain visible in red
- Run computation-panel workflows for channel mean traces and Epileptogenicity Index (EI)
- Enter seizure onset/offset and baseline/ictal windows manually for EI runs
- Review EI results in a sortable summary table and heatmap
- Save and reopen project state
- Warn about unsaved review changes before closing
- Use resizable computation, annotation, and PSD panels for selected channels

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
pip install -r requirements.txt
```

The project is typically run from the local virtual environment in `.venv`.

## Running

From the project root folder:

```bash
python main.py
```

If you are using the project virtual environment on Windows:

```bash
.\.venv\Scripts\python.exe main.py
```

## User Guide

The full guide is bundled at:

`app/docs/user_guide.md`

It is also available inside the application from **Help > User Guide**.
