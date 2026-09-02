# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

"""pyhfo_pybrain classifier entry point."""

from app.computation.hfo.classification._pyhfo_binary_common import (
    classify_pyhfo_pybrain_candidate_pool,
)

classify_pyhfo_pybrain = classify_pyhfo_pybrain_candidate_pool

__all__ = [
    "classify_pyhfo_pybrain",
]
