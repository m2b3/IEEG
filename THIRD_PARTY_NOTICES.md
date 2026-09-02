<!-- SPDX-FileCopyrightText: 2026 The Project Authors -->
<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Third-Party Notices

The root AGPL-3.0-only license and SPDX headers apply only to material for
which the Project Authors hold the necessary rights. They do not replace or
override third-party terms.

This audit covers the current repository and its Git history. Upstream
revisions inspected in September 2026 are recorded below so the findings can
be reproduced.

## Recruitment Energy Index: BrainQuake and IEEG_EI

Affected files:

- `app/computation/rei/algorithm.py`
- `app/computation/rei/algorithm.pyi`

The core `compute_hfer`, `determine_threshold_onset`, and
`compute_ei_index` implementation follows this provenance chain:

1. [`HongLabTHU/BrainQuake`](https://github.com/HongLabTHU/BrainQuake),
   `BrainQuake/client_ictal.py`, revision
   `398492abb6440b86d447a8f6a7d83920bf241009`.
2. [`allucas/IEEG_EI`](https://github.com/allucas/IEEG_EI),
   `ei_main_gui.py`, revision
   `2dbd30bebb37e84ff6e06a168bca542ed0e2fc33`.
3. The adapted REI implementation in this repository.

A source comparison confirms that IEEG_EI retained substantial BrainQuake EI
code, with parameter and GUI changes, and that the present REI core is a
further adaptation of that code.

BrainQuake is licensed under Apache-2.0. Its repository does not identify a
specific copyright holder in a copyright notice; it is published by the
HongLabTHU organization and licensed through its contributors. BrainQuake
contains no `NOTICE` file. Apache-2.0 requires recipients of source or object
distributions to receive the license, retain applicable copyright, patent,
trademark, and attribution notices, and mark modified files. The required
license copy is [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt).
Apache-2.0 is compatible with AGPLv3 when its notice requirements are
preserved.

IEEG_EI identifies Alfredo Lucas through its Git history but contains no
license file, copyright notice, or explicit reuse grant. Public availability
is not a license. The licensing of Alfredo Lucas's changes, and therefore the
complete permission chain for the present REI files, remains unresolved.
Those files have not been marked AGPL-3.0-only.

## Spike-Gamma, John Thomas, Sayeed, and Radek Janča

Source: [`Lab-Frauscher/Spike-Gamma`](https://github.com/Lab-Frauscher/Spike-Gamma),
revision `487ae8bb85394b455560c639b45c5bc102b486b6`, published through the
ANPHY Lab account.

Affected files:

- `app/computation/gamma_spike/original_algorithm/compute_gamma.py`
- `app/computation/gamma_spike/original_algorithm/compute_spike_boundary.py`
- `app/computation/gamma_spike/original_algorithm/postprocessing.py`
- `app/computation/gamma_spike/original_algorithm/spike_detector_hilbert_v25.py`
- helper/refactoring files in the same directory
- `app/computation/gamma_spike/wire_algorithm.py`, whose compatibility logic
  is closely tied to the translated workflow

The upstream file notices identify:

- `compute_gamma.m`: John Thomas, `john007@e.ntu.edu.sg`, created
  1 July 2021.
- `compute_spike_boundary.m`: John Thomas,
  `john007@e.ntu.edu.sg`, created 1 March 2022.
- `postprocessing.m`: Sayeed and John Thomas, created 23 April 2021; the
  header states that reuse requires contacting the authors.
- `spike_detector_hilbert_v25.m`: Radek Janča (`ISARG`), dated
  19 September 2013, and citing the Janča et al. detector publication.

The repository does not state who legally owns these copyrights. Its README
provides only a research-use statement: academic and research use is intended,
and commercial or non-academic users must contact the authors. Several source
headers separately require contact for reuse. There is no standard license
text or explicit permission to redistribute, modify, or sublicense the code.

These terms do not supply permission to relicense the translations under
AGPL-3.0-only and appear incompatible with unrestricted AGPL distribution.
No SPDX declaration was added to these files and no invented license copy was
created. Written permission or a clarified upstream license is required before
distribution.

## HFO candidate detectors: HFODetector

The application imports `HFODetector==0.0.25` from
[`roychowdhuryresearch/HFO_Detector`](https://github.com/roychowdhuryresearch/HFO_Detector).
The package is not vendored, but `app/computation/hfo/detectors/` and the HFO
orchestration reproduce the parameterization used by Omni-iEEG.

The installed package metadata names Xin Chen and Hoyoung Chung as authors.
Its included license identifies UCLA as the institution/copyright holder and
names Yipeng Zhang, Xin Chen, Hoyoung Chung, Lawrence Liu, Yuanyi Ding, Hiroki
Nariai, and Vwani Roychowdhury as creators.

The UCLA Academic Software License permits use and derivative works only by
academic or nonprofit researchers for educational or academic research. It
prohibits further transfer of the software and derivatives, requires a
specified acknowledgment in academic publications, disclaims warranties, and
directs commercial entities to obtain a separate license. A copy is
at [`LICENSES/UCLA-Academic-Software-License.txt`](LICENSES/UCLA-Academic-Software-License.txt).

Because HFODetector is installed separately, its code is not being relicensed
by this repository. Its non-transfer and field-of-use restrictions are
nevertheless incompatible with AGPL distribution of a bundled application and
must be reviewed before distributing an installer or environment containing
the package.

## pyHFO classifiers and checkpoints

Sources:

- [`roychowdhuryresearch/pyHFO`](https://github.com/roychowdhuryresearch/pyHFO),
  revision `c1c7d3fec990e8661604080664766dc1297cbfdc`.
- [`roychowdhuryresearch/HFO-Classification`](https://github.com/roychowdhuryresearch/HFO-Classification),
  `Pruning-pipeline`, revision
  `f96ef79894e3752bf185d60ceb1eb86a48f58f21`.

Affected code is under
`app/computation/hfo/classification/_pyhfo_binary_common/` and related
classifier/orchestration modules.

The following bundled files are byte-identical to pyHFO upstream checkpoints:

- `model_a.tar` — SHA-256
  `3fea070cb08e8789a06a7db3cd7210ba56ac30fd6c21c7517fb58336872b041c`
- `model_s.tar` — SHA-256
  `5b753b3373c2da1e85d7856e27674178950f5c5c02d879ab30a0308d136cb4ba`

pyHFO and the upstream Pruning pipeline use the UCLA Academic Software
License described above. UCLA is the stated institution/copyright holder; the
license acknowledgment names Yipeng Zhang, Xin Chen, Hoyoung Chung, Lawrence
Liu, Yuanyi Ding, Hiroki Nariai, and Vwani Roychowdhury.

The license's express prohibition on further transfer means that including
these checkpoints in a publicly distributable repository is not authorized by
that license. It is also incompatible with AGPL-3.0-only. Adding the upstream
license copy preserves the terms but does not cure the redistribution issue.
The checkpoints and derived classifier code remain unmarked and require
permission or removal/replacement before distribution.

## eHFO implementation and checkpoints

Source: [`roychowdhuryresearch/HFO-Classification`](https://github.com/roychowdhuryresearch/HFO-Classification),
`Discover_eHFO`, revision
`f96ef79894e3752bf185d60ceb1eb86a48f58f21`.

Affected code is under `app/computation/hfo/classification/ehfo/` and related
classifier/orchestration modules. The following bundled files are
byte-identical to the upstream Discover_eHFO checkpoints:

- `artifacts.pth` — SHA-256
  `bb9e5a76cc41bdd384b3ee2a006f09bd1eca2552b358ec4c20dcf96ae0f0779f`
- `spikes.pth` — SHA-256
  `6bded2e3b62ad4022561cefd3e5d153a0fe7957f55c5f88e419cf974b44a0490`
- `eHFOs.pth` — SHA-256
  `bb2c8cd0f1f4579643fe932eddba3074f758807e2d5f7b6b1fbd90a31cf15f01`

Discover_eHFO is MIT-licensed, copyright © 2020 Yipeng Zhang. The MIT license
requires its copyright and permission notice to accompany copies or
substantial portions. The required copy is
[`LICENSES/eHFO-MIT.txt`](LICENSES/eHFO-MIT.txt). MIT is compatible with
AGPLv3 when its notice is retained.

Some eHFO integration also mirrors Omni-iEEG code, whose separate licensing
uncertainty remains applicable.

## Omni-iEEG-derived integration

Source: [`Omni-iEEG/Omni-iEEG`](https://github.com/Omni-iEEG/Omni-iEEG),
revision `57c22a75a59b5c3a98006806ad42000f6a3fa5b6`.

Affected or potentially affected files include:

- `app/computation/hfo/algorithm.py`
- `app/computation/hfo/classification/_pyhfo_binary_common/`
- `app/computation/hfo/classification/ehfo/`
- `app/computation/hfo/detectors/omni_hfo_detector.py`
- `app/computation/hfo/preprocessing/omni.py`
- `app/computation/hfo/preprocessing/pybrain.py`

The upstream repository identifies the Omni-iEEG organization as author in
`setup.py`, but contains no license file, copyright notice, or explicit reuse
grant. Its public availability does not authorize copying, modification, or
redistribution. No license copy can be supplied. These files remain unmarked
and require clarification or permission from Omni-iEEG before distribution.

## External packages and CI actions not vendored here

The remaining packages in `requirements.txt` are installed separately and
retain the license files supplied in their own distributions. They are not
relicensed by this repository. The direct dependencies report these licenses:

| Component | Upstream copyright/project | License |
| --- | --- | --- |
| Colorama | Colorama contributors | BSD-3-Clause |
| MNE-Python | MNE-Python contributors | BSD-3-Clause |
| Matplotlib | Matplotlib Development Team | Matplotlib License |
| NumPy | NumPy Developers | BSD-3-Clause |
| pandas | pandas development team | BSD-3-Clause |
| pyqtgraph | PyQtGraph contributors | MIT |
| PySide6, Shiboken6 | The Qt Company and contributors | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| SciPy | SciPy Developers | BSD-3-Clause |
| scikit-image | scikit-image contributors | BSD-3-Clause, with separately licensed bundled portions |
| tifffile | Christoph Gohlke and contributors | BSD-3-Clause |
| safetensors | Hugging Face and contributors | Apache-2.0 |
| PyTorch, torchvision | PyTorch contributors | BSD-3-Clause |

Their package-level notices must accompany any redistributed binary
environment. The PySide LGPL option also requires normal LGPL compliance,
including allowing replacement/relinking of the covered libraries.

The workflow references `actions/checkout`, `actions/configure-pages`,
`actions/upload-pages-artifact`, and `actions/deploy-pages`. These are
remote GitHub Actions, not copied into this repository. Their upstream
projects are MIT-licensed by GitHub, Inc.; their own distributions retain the
required notices.
