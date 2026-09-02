# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

"""Backward-compatible imports for HFO input preparation."""

from app.computation.hfo.preprocessing.omni import (
    HFOInput,
    prepare_hfo_input_from_array,
    prepare_hfo_input_from_file,
)

__all__ = [
    "HFOInput",
    "prepare_hfo_input_from_array",
    "prepare_hfo_input_from_file",
]
