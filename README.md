# iEEG Tool

> [!WARNING]
> **Work in progress.** This software is under active development and has not
> been validated as a medical device. It is intended for research and expert
> review, not for diagnosis or treatment decisions.

## What is this software?

iEEG Tool is a desktop application for reviewing intracranial EEG recordings.
It provides a responsive multichannel viewer, montage and rereferencing tools,
annotations, display filters, PSD and scalogram views, project saving, and
analysis-result review and export.

The complete interface and workflow documentation is in the
[User Guide](app/docs/user_guide.html), also available from
**Help > User Guide** inside the application.

## Included analysis algorithms

### Recruitment Energy Index (REI)

REI ranks channels using spectral changes around seizure onset and their
recruitment delay. Bipolar montage is recommended. This implementation adapts
the open `IEEG_EI` implementation; it is a review aid rather than a clinical
conclusion.

References:

- [Bartolomei, Chauvel & Wendling (2008), *Brain*](https://doi.org/10.1093/brain/awn111)
- [`allucas/IEEG_EI`](https://github.com/allucas/IEEG_EI)

### Gamma Spike

Gamma Spike detects interictal spikes, estimates their boundaries, measures
preceding 30-100 Hz activity, and separates gamma-positive from non-gamma
spikes. The application contains a Python translation of the Lab-Frauscher
MATLAB workflow and uses the Janca Hilbert-envelope spike detector.

References:

- [`Lab-Frauscher/Spike-Gamma`](https://github.com/Lab-Frauscher/Spike-Gamma)
- [Janca et al. (2015), *Brain Topography*](https://doi.org/10.1007/s10548-014-0379-1)

### High-Frequency Oscillations (HFO)

HFO analysis combines STE, MNI, and Hilbert candidate detectors with selectable
classification routes:

- `pyhfo_pybrain` (default): native-sampling pyHFO/pyBrain route, 80-500 Hz
- `pyhfo_omni_legacy`: Omni-compatible pyHFO route, 80-300 Hz at 1000 Hz
- `eHFO`: Omni-compatible eHFO route, 80-300 Hz at 1000 Hz

The classifiers distinguish artifacts, non-spike HFOs, spike-HFOs, and, for the
eHFO route, eHFO and spike-eHFO events. Results remain available for expert
review and manual correction.

References:

- [`HFODetector` candidate-detector package](https://pypi.org/project/HFODetector/)
- [`roychowdhuryresearch/pyHFO`](https://github.com/roychowdhuryresearch/pyHFO)
- [pyHFO `pyBrain` branch](https://github.com/roychowdhuryresearch/pyHFO/tree/pyBrain)
- [`Omni-iEEG/Omni-iEEG`](https://github.com/Omni-iEEG/Omni-iEEG)

## Installation

Use a 64-bit installation of **Python 3.10 or 3.11**. Python 3.11 is
recommended. Install [Git](https://git-scm.com/install/) and
[Python](https://www.python.org/downloads/) first.

Clone the repository and enter its folder:

```bash
git clone https://github.com/m2b3/I_EEG.git
cd I_EEG
```

If you downloaded a ZIP instead, extract it and open a terminal in the extracted
`I_EEG` folder.

### Windows PowerShell

Create the virtual environment **before** installing the requirements:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Using the venv's Python directly avoids PowerShell activation-policy errors.
Launch the application with:

```powershell
.\.venv\Scripts\python.exe main.py
```

### macOS

Create a separate, machine-local environment. Do not copy `.venv` between
Windows and macOS.

```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python main.py
```

If your Python 3.11 command is named `python3`, use that instead of
`python3.11` when creating the venv.

## HFO files and downloads

A normal Git clone includes the five required classifier checkpoints:

```text
app/computation/hfo/checkpoints/pyhfo_legacy_binary/model_a.tar
app/computation/hfo/checkpoints/pyhfo_legacy_binary/model_s.tar
app/computation/hfo/checkpoints/ehfo/artifacts.pth
app/computation/hfo/checkpoints/ehfo/spikes.pth
app/computation/hfo/checkpoints/ehfo/eHFOs.pth
```

No separate model download is normally required. If any file is missing, get it
from the project's
[HFO checkpoint folder](https://github.com/m2b3/I_EEG/tree/main/app/computation/hfo/checkpoints)
or clone the repository again.

`HFODetector` is also required for HFO candidate detection. It is installed
automatically by `requirements.txt`; its package page is
[here](https://pypi.org/project/HFODetector/).

After installation, verify the environment on Windows:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "from HFODetector import hil, mni, ste; import PySide6, mne, pyqtgraph, torch, torchvision, skimage, safetensors; print('dependency check ok')"
```

On macOS, use `./.venv/bin/python` in place of
`.\.venv\Scripts\python.exe`.

## Updating an existing installation

After pulling a newer version, reinstall the requirements because dependencies
may have changed:

```bash
git pull --ff-only
```

Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS:

```bash
./.venv/bin/python -m pip install -r requirements.txt
```

## More help

Open **Help > User Guide** in the application, or open
[`app/docs/user_guide.html`](app/docs/user_guide.html) directly in a browser.
