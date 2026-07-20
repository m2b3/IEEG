Gamma Spike Export

Files:
- gamma_channel_summary.csv
  One row per channel. Includes total spikes, gamma-positive spikes,
  non-gamma spikes, gamma rate, and mean gamma measurements.

- gamma_spike_events.csv
  One row per detected spike. Includes channel, event time, spike
  boundaries, gamma power, gamma frequency, gamma duration, and errors.
  Sample columns use 1-based indexing, and time columns are derived from those
  exported samples, to match the original Python2 output.

- gamma_metadata.json
  Records the analyzed file, analysis window, sampling frequency, segmented
  processing settings, notch filter setting, and spike counts.

Notes:
- Spikes are exported only inside the selected analysis window.
- The detector window is exactly the selected analysis window; extra context is
  used only for boundary and gamma detail measurements.
- Gamma measurements use the notch setting selected before running the algorithm.
- No gamma heatmap figures are exported.
- Time values are in seconds.
