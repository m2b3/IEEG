# Core Spike-Gamma Algorithm

This folder contains the translated scientific building blocks used by the app.

It was derived from:

- the validated `Python2` core algorithm translation from `matlab2`
- the segmented long-recording validation pipeline

The app-level GUI wiring, chunking, metadata, and export result objects live in
`app/computation/gamma_spike/wire_algorithm.py`.

## Main Components

- `spike_detector_hilbert_v25.py`
  Detects candidate spikes from multichannel data.

- `postprocessing.py`
  Cleans detector events and organizes retained spikes by channel.

- `compute_spike_boundary.py`
  Finds P1, N1, and N2 boundaries around one spike.

- `compute_gamma.py`
  Measures gamma power, frequency, and duration around one spike.

- `build_gamma_masks.py`
  Builds validity masks used during gamma measurement.

- `select_max_gamma_candidate.py`
  Selects the strongest candidate gamma event.

## GUI Pipeline

The computation panel calls:

```python
compute_gamma_spike_segmented_for_gui(...)
```

from `wire_algorithm.py`.

That wrapper reads GUI-selected data in 10-minute chunks with 10-second context,
runs the detector, merges central events, postprocesses once globally, then
computes spike boundaries and gamma measurements.

The old file-based batch runner has been removed to avoid maintaining two
segmentation implementations.
