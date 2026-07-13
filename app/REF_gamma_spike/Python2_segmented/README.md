# Python2 Segmented Spike-Gamma Pipeline

This folder is the clean export target for integration into your software.

It merges:

- the validated `Python2` core algorithm translation from `matlab2`
- the segmented long-recording pipeline from `Python_segment`

Validation scripts and large output CSVs are intentionally not included here.

## Main Import

```python
from Python2_segmented import run_segmented_recording

result = run_segmented_recording(
    recording_path,
    chunk_minutes=10.0,
    context_seconds=10.0,
    filter_context_seconds=30.0,
    max_spikes_for_boundary_gamma=None,
)
```

`result` contains:

- `step1`: raw Janca detector detections
- `step2`: postprocessed retained spikes by channel
- `qc`: postprocessing QC counts
- `step3`: spike boundaries
- `step4`: gamma measurements
- `summary`: one-row processing summary

## Command-Line Use

From the repo root:

```powershell
python -m Python2_segmented.run_segmented_file "path\to\recording.ieeg" --output-dir "path\to\outputs"
```

Useful options:

```powershell
--chunk-minutes 10
--context-seconds 10
--filter-context-seconds 30
--max-spikes-for-boundary-gamma 250
```

Use `--max-spikes-for-boundary-gamma` only for validation/debugging. For full processing, omit it.

## File Support

The pipeline can read:

- `.edf`
- `.fif`
- `.ieeg` JSON metadata files that point to a source raw file

## Validated Defaults

Use `context_seconds=10.0` for segmented processing. In the 30-minute multi-chunk validation, 5 seconds left two chunk-boundary event differences, while 10 seconds matched full Python2 event locations and downstream outputs.

## Folder Roles

- `Python2_segmented`: clean export/integration code
- `Python2`: preserved validated core translation
- `Python_segment`: preserved segmented validation scripts and generated outputs
- `testing2`: preserved MATLAB/Python validation evidence
