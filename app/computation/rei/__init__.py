# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

"""Recruitment Energy Index (REI) computation."""

from app.computation.rei.algorithm import (
    EIChannelResult,
    EIComputationResult,
    compute_ei_for_gui,
    validate_gui_ei_timing,
)

__all__ = [
    "EIChannelResult",
    "EIComputationResult",
    "compute_ei_for_gui",
    "validate_gui_ei_timing",
]
