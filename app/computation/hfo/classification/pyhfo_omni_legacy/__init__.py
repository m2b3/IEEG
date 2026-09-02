# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

"""pyhfo_omni_legacy classifier entry point."""

from app.computation.hfo.classification._pyhfo_binary_common import (
    classify_pyhfo_omni_legacy_batch,
)

classify_pyhfo_omni_legacy = classify_pyhfo_omni_legacy_batch

__all__ = [
    "classify_pyhfo_omni_legacy",
]
