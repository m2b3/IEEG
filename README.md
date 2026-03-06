# I_EEG

This is an EEG / iEEG viewer built with PySide6, PyQtGraph and MNE.

It allows visualisation and interaction with multi-channel electrophysiological recordings (EDF, FIF, BDF, etc.).

The viewer is designed for interactive exploration and review of EEG/iEEG data.

---
# Main Features

Multi-channel EEG/iEEG visualization

Fast interactive navigation

Channel selection and scaling

Manual event/segment annotation

Persistent review projects (.ieeg)

Restoration of annotations and review state

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



# 1.2 Annotation System

The viewer supports manual signal annotation for review.

Users can mark events such as: Epileptic Spike, Ripple, Fast ripple, Artifact, Bad segment, Other

Annotation workflow :

- Open a dataset or project

- Select Edit → Annotate

- Choose:

    annotation type

    scope

    optional note

- Drag on the signal to create an annotation

Annotations appear as semi-transparent overlays on the signal and are listed in the Annotations Dock. They can be edited, deleted, navigated from the annotation list dock pannel

# 1.3. Project System

Review data is stored using project files.

Raw EEG files remain unchanged.

The viewer saves review state in a .ieeg project file (JSON format).

Project files store:

- path to the raw EEG file

- annotations

- hidden channels

- bad channels

Example structure:

{
  "format": "ieeg-review-project",
  "version": 1,
  "source": {
    "raw_file": "path/to/eeg.edf"
  },
  "review": {
    "annotations": [...],
    "hidden_channels": [...],
    "bad_channels": [...]
  }
}
# 1.4 Project workflow

File → New Project

    Select a raw EEG file

    Create a new .ieeg project

File → Open Project

    Load an existing project

    Restore annotations and review state

File → Save

    Update the current project

File → Save As

    Save the current review state to a new project file

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

File → New Project or Open Project


## 4.2 Navigation
Scroll channels : Mouse wheel or trackpad scroll

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



# 5. Testing the program
To test the viewer:

1 Launch the application

2 Create a new project from an EEG file

3 Verify:

channels scroll correctly

zoom works with Shift + wheel

channel selection highlights correctly

timeline slider synchronizes with the plot

annotations can be created and edited

projects save and reload correctly


# 6. Project status

⚠️ The project is under active development.
