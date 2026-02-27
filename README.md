# I_EEG

This is an EEG / iEEG viewer built with PySide6, PyQtGraph and MNE.

It allows visualisation and interaction with multi-channel electrophysiological recordings (EDF, FIF, BDF, etc.).

---

# 1. How the Program Works

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

---

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
# 4. How to Use the Program
## 4.1 Loading a File

File → Open

Select an EDF / FIF / supported EEG file

## 4.2 Main Interactions
Scrolling : Mouse wheel or two-finger scroll → scroll through channels

Zoom

- Shift + wheel over signal area → zoom time range

- Shift + wheel over label area → zoom number of visible channels

Channel Selection

- Click on a signal trace or channel label to select it

- Selected channel is highlighted

## 4.3 Toolbar Controls

Time Range → controls visible time window

Channels → controls number of visible channels

Amplitude → controls vertical scaling (± µV)

Fichier md if required / too long

# 5. Testing the program
To test the viewer:

1 Launch the application

2 Open a sample EEG file

3 Verify:

Channels scroll correctly

Zoom works with Shift + wheel

Channel selection highlights properly

Timeline slider synchronizes with the plot


# 6. Project status

⚠️ The project is under active development.
