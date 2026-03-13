# iEEG Tool

A desktop application for reviewing intracranial EEG (iEEG) recordings with interactive visualization, rereferencing tools, annotation support, and project-based review workflows.

Built with **PySide6**, **PyQtGraph**, and **MNE**.

## Overview

iEEG Tool is designed to support efficient clinical or research review of multichannel EEG/iEEG data. The viewer provides a stacked multichannel display, interactive navigation, rereferencing options, annotation tools, and a project workflow that preserves review state across sessions.

The application is built around an MNE-based loading pipeline and a PySide6 / PyQtGraph user interface.

The program is structured around a main viewer that:

- loads EEG data using MNE
- displays channels stacked vertically
- shows a time window of the recording
- allows interactive navigation, scrolling, zooming, and channel selection

Main components include:

- `main_window.py` — application logic and UI coordination
- `plot.py` — `MultiChannelViewer` rendering and interaction engine
- `menus.py` — menu structure
- `console_viewer.py` — console output window

Rendering is optimized to:

- load only visible channels
- load only the visible time window
- decimate data for responsiveness

## Main features

- Load EEG/iEEG recordings from common electrophysiology formats
- Scroll through multichannel data with adjustable time window, channel count, and gain
- Switch between multiple rereferencing modes:
  - Monopolar
  - Bipolar
  - Average
  - Median
  - Common reference
- Automatically generate bipolar montages from channel labels
- Edit bipolar pairs manually
- Mark channels as hidden or bad
- Add and edit annotations directly in the viewer
- Use zoom selection for rectangular zoom-in review
- Save and reopen project state
- Use a dedicated computation panel for selected channels

## Installation

### Install Python

Recommended version:

- Python 3.10
- Python 3.11

Download from:
https://www.python.org/downloads/

### Install dependencies

From the project root folder:

```bash
pip install -r requirements.txt