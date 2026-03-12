# I_EEG

A desktop application for reviewing intracranial EEG (iEEG) recordings with interactive visualization, rereferencing tools, annotation support, and project-based review workflows.

Built with PySide6, PyQtGraph and MNE.

## Overview

iEEG Tool is designed to support efficient clinical or research review of multichannel EEG/iEEG data. The viewer provides a stacked multichannel display, interactive navigation, rereferencing options, annotation tools, and a project workflow that preserves review state across sessions.

The application is built around an MNE-based loading pipeline and a PySide6 / PyQtGraph user interface.

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
- Save and reopen project state
- Use a dedicated computation panel for selected channels

## Supported file formats

The current loading pipeline supports:

- `.edf`
- `.bdf`
- `.fif`
- `.vhdr`
- `.set`
- `.cnt`

# 1.1 How the Program Works

I_EEG is structured around a main viewer that:

- Loads EEG data using MNE
- Displays channels stacked vertically
- Shows a time window of the recording
- Allows interactive navigation (scrolling, zooming, channel selection)

Main components:

- `main_window.py` → application logic and UI coordination
- `plot.py` → `MultiChannelViewer` (rendering and interaction engine)
- `menus.py` → menu structure
- `console_viewer.py` → console output window

Rendering is optimized to:
- Only load visible channels
- Only load visible time window
- Decimate data for responsiveness






# 2. Installation

## 2.1 Install Python

Recommended version:

Python 3.10 or 3.11

Download from:
https://www.python.org/downloads/

---

## 2.2 Install Dependencies

From the project root folder:

```bash
pip install -r requirements.txt
```

# 3. Run the program

From the project root : 
```bash
python main.py
```


# 6. Project status

⚠️ The project is under active development.



For detailed usage instructions, see [User Guide](docs/user_guide.md).