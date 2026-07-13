"""
Python translation of Matlab_version/spike_detector_hilbert_v25.m.

This is a first faithful port of the Janca Hilbert-envelope detector used by
the spike-gamma workflow.  The immediate goal is behavioral validation against
the MATLAB implementation, so the code keeps several MATLAB-like names and
stages instead of hiding them behind a cleaner API too early.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import shlex
from typing import Optional, Tuple

import numpy as np
from scipy import signal, special
from scipy.interpolate import CubicSpline, interp1d


@dataclass
class DetectorSettings:
    bandwidth: Tuple[float, float] = (10.0, 60.0)
    k1: float = 3.65
    k2: Optional[float] = None
    k3: float = 0.0
    buffering: float = 300.0
    main_hum_freq: float = 50.0
    beta: float = math.inf
    beta_win: float = 20.0
    beta_ar: int = 12
    filter_type: int = 1
    discharge_tol: float = 0.005
    polyspike_union_time: float = 0.12
    decimation: float = 200.0
    ti_switch: int = 1
    winsize: Optional[float] = None
    noverlap: Optional[float] = None

    def __post_init__(self) -> None:
        if self.k2 is None:
            self.k2 = self.k1


@dataclass
class DetectorOutput:
    pos: np.ndarray
    dur: np.ndarray
    chan: np.ndarray
    con: np.ndarray
    weight: np.ndarray
    pdf: np.ndarray


@dataclass
class Discharges:
    MV: np.ndarray
    MA: np.ndarray
    MP: np.ndarray
    MD: np.ndarray
    MW: np.ndarray
    MPDF: np.ndarray
    MRAW: np.ndarray


def parse_settings(settings: str | None) -> DetectorSettings:
    cfg = DetectorSettings()
    if not settings:
        return cfg

    tokens = shlex.split(settings)
    if len(tokens) % 2:
        raise ValueError(f"settings must be flag/value pairs: {settings!r}")

    for key, value in zip(tokens[0::2], tokens[1::2]):
        key = key.lstrip("-").lower()
        val = float(value)

        # MATLAB docs mention -bl/-bh in places, while parser used -fl/-fh.
        # Support both so the README example behaves as intended.
        if key in {"fl", "bl"}:
            cfg.bandwidth = (val, cfg.bandwidth[1])
        elif key in {"fh", "bh"}:
            cfg.bandwidth = (cfg.bandwidth[0], val)
        elif key == "k1":
            cfg.k1 = val
            if cfg.k2 is None:
                cfg.k2 = val
        elif key == "k2":
            cfg.k2 = val
        elif key == "k3":
            cfg.k3 = val
        elif key == "w":
            cfg.winsize = val
        elif key == "n":
            cfg.noverlap = val
        elif key == "buf":
            cfg.buffering = val
        elif key == "h":
            cfg.main_hum_freq = val
        elif key == "b":
            cfg.beta = val
        elif key == "bw":
            cfg.beta_win = val
        elif key == "br":
            cfg.beta_ar = int(val)
        elif key == "ft":
            cfg.filter_type = int(val)
        elif key == "dt":
            cfg.discharge_tol = val
        elif key == "pt":
            cfg.polyspike_union_time = val
        elif key == "dec":
            cfg.decimation = val
        elif key == "ti":
            cfg.ti_switch = int(val)

    if cfg.k2 is None:
        cfg.k2 = cfg.k1
    return cfg


def spike_detector_hilbert_v25(
    data: np.ndarray,
    fs: float,
    settings: str | DetectorSettings | None = None,
) -> tuple[DetectorOutput, Discharges, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Run the Janca Hilbert-envelope spike detector.

    Parameters
    ----------
    data:
        Signal matrix shaped samples x channels.
    fs:
        Sampling frequency in Hz.
    settings:
        MATLAB-style settings string, e.g. "-bl 10 -bh 60 -h 60 -k1 3.65 -dec 200".

    Returns
    -------
    out, discharges, d_decim, envelope, background, envelope_pdf
        MATLAB-like outputs. Channel numbers in ``out.chan`` are 1-based to
        match MATLAB.
    """

    cfg = settings if isinstance(settings, DetectorSettings) else parse_settings(settings)
    d = _as_2d(data)

    winsize = cfg.winsize if cfg.winsize is not None else 5 * fs
    noverlap = cfg.noverlap if cfg.noverlap is not None else 4 * fs
    if cfg.decimation == 0:
        cfg.decimation = fs
    if cfg.bandwidth[1] > cfg.decimation:
        raise ValueError("filter -fh frequency is bigger than fs/2")

    # First pass: process as one segment. The MATLAB code supports buffered
    # matfile processing; direct ndarray processing is enough for validation.
    d_decim, envelope, background, discharges, out, envelope_pdf, _ = _spike_detector(
        d, fs, int(round(winsize)), int(round(noverlap)), cfg
    )

    keep = (out.pos > 2.0) & (out.pos < (d.shape[0] / fs - 2.0))
    out = _filter_out(out, keep)

    if discharges.MP.size:
        mp_min = np.nanmin(discharges.MP, axis=1)
        keep_d = (mp_min > 2.0) & (mp_min < (d.shape[0] / fs - 2.0))
        discharges = _filter_discharges(discharges, keep_d)

    return out, discharges, d_decim, envelope, background, envelope_pdf


def _spike_detector(
    d: np.ndarray,
    fs: float,
    winsize: int,
    noverlap: int,
    cfg: DetectorSettings,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, Discharges, DetectorOutput, np.ndarray, float]:
    decimation = cfg.decimation
    r_factor = fs / decimation

    if r_factor > 1 or decimation != fs:
        winsize_sec = winsize / fs
        noverlap_sec = noverlap / fs
        dec_iterations = max(1, int(math.ceil(math.log10(r_factor)))) if r_factor > 0 else 1

        for i in range(dec_iterations):
            if i == dec_iterations - 1:
                fs_out = decimation
            else:
                fs_out = round(fs / (r_factor ** (1 / dec_iterations)))
            d = _resample_columns(d, fs_out, fs)
            fs = fs_out

        winsize = int(round(winsize_sec * fs))
        noverlap = int(round(noverlap_sec * fs))

    if noverlap < 1:
        step = max(1, int(round(winsize * (1 - noverlap))))
    else:
        step = max(1, int(winsize - noverlap))
    index = np.arange(0, max(1, d.shape[0] - winsize + 1), step, dtype=int)
    if index.size == 0:
        index = np.array([0], dtype=int)
        winsize = d.shape[0]

    d = _filt_hum(d, fs, cfg.main_hum_freq, cfg.bandwidth)
    b_hp, a_hp = signal.butter(2, 2 * 1 / fs, btype="high")
    d_decim = _filtfilt(b_hp, a_hp, d)

    beta_mask = None
    if cfg.beta < fs / 2 and cfg.beta_win > 0:
        beta_mask = _beta_detect(d, fs, cfg.beta, cfg.beta_win, cfg.beta_ar)

    d_band = _filtering(d, fs, cfg)

    n_samples, n_channels = d_band.shape
    envelope = np.zeros_like(d_band, dtype=float)
    markers_high = np.zeros_like(d_band, dtype=bool)
    markers_low = np.zeros_like(d_band, dtype=bool)
    n_bg = 1 if cfg.k2 == cfg.k1 else 2
    background = np.zeros((n_samples, n_channels, n_bg), dtype=float)
    envelope_cdf = np.zeros_like(d_band, dtype=float)
    envelope_pdf = np.zeros_like(d_band, dtype=float)

    for ch in range(n_channels):
        if np.all(d_band[:, ch] == 0):
            continue
        result = _one_channel_detect(
            d_band[:, ch],
            fs,
            index,
            winsize,
            cfg.k1,
            float(cfg.k2),
            cfg.k3,
            cfg.polyspike_union_time,
            cfg.ti_switch,
            d_decim[:, ch],
        )
        (
            envelope[:, ch],
            markers_high[:, ch],
            markers_low[:, ch],
            background[:, ch, : result[3].shape[1]],
            envelope_cdf[:, ch],
            envelope_pdf[:, ch],
        ) = result

    edge = int(round(fs))
    if n_samples > 2 * edge:
        markers_high[:edge, :] = False
        markers_high[-edge:, :] = False
        markers_low[:edge, :] = False
        markers_low[-edge:, :] = False

    tail = int(math.ceil(cfg.discharge_tol * fs + 1))
    if tail > 0:
        markers_high[-tail:, :] = False
        markers_low[-tail:, :] = False

    if beta_mask is not None:
        markers_high[beta_mask] = False
        markers_low[beta_mask] = False

    out = _make_out(markers_high, markers_low, envelope_cdf, envelope_pdf, fs, cfg)
    discharges = _make_discharges(
        out, d_band, d_decim, envelope, background, envelope_cdf, envelope_pdf, fs, cfg
    )

    return d_decim, envelope, background, discharges, out, envelope_pdf, r_factor


def _one_channel_detect(
    d: np.ndarray,
    fs: float,
    index: np.ndarray,
    winsize: int,
    k1: float,
    k2: float,
    k3: float,
    polyspike_union_time: float,
    ti_switch: int,
    d_decim: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    envelope = np.abs(signal.hilbert(d))

    phat = np.zeros((len(index), 2), dtype=float)
    for k, start in enumerate(index):
        stop = min(start + winsize, envelope.size)
        segment = envelope[start:stop]
        segment = segment[segment > 0]
        if segment.size == 0:
            phat[k, :] = 0.0
        else:
            logged = np.log(segment)
            phat[k, 0] = np.mean(logged)
            phat[k, 1] = np.std(logged, ddof=1)

    r = envelope.size / max(len(index), 1)
    n_average = winsize / fs
    smooth_len = int(round(n_average * fs / r)) if r else 0
    if smooth_len > 1 and phat.shape[0] > 3 * smooth_len:
        kernel = np.ones(smooth_len) / smooth_len
        phat = signal.filtfilt(kernel, [1.0], phat, axis=0)

    phat_int = _interpolate_phat(phat, index, winsize, envelope.size)
    sigma = np.maximum(phat_int[:, 1], np.finfo(float).eps)

    lognormal_mode = np.exp(phat_int[:, 0] - sigma**2)
    lognormal_median = np.exp(phat_int[:, 0])
    lognormal_mean = np.exp(phat_int[:, 0] + (sigma**2) / 2)

    high = k1 * (lognormal_mode + lognormal_median) - k3 * (lognormal_mean - lognormal_mode)
    if k2 == k1:
        prah_int = high[:, None]
    else:
        low = k2 * (lognormal_mode + lognormal_median) - k3 * (lognormal_mean - lognormal_mode)
        prah_int = np.column_stack([high, low])

    safe_envelope = np.maximum(envelope, np.finfo(float).tiny)
    envelope_cdf = 0.5 + 0.5 * special.erf(
        (np.log(safe_envelope) - phat_int[:, 0]) / np.sqrt(2 * sigma**2)
    )
    envelope_pdf = np.exp(-0.5 * ((np.log(safe_envelope) - phat_int[:, 0]) / sigma) ** 2)
    envelope_pdf /= safe_envelope * sigma * np.sqrt(2 * np.pi)

    markers_high = _local_maxima_detection(
        envelope, prah_int[:, 0], fs, polyspike_union_time, ti_switch, d_decim
    )
    markers_high = _detection_union(markers_high, envelope, polyspike_union_time * fs)

    if k2 == k1:
        markers_low = markers_high.copy()
    else:
        markers_low = _local_maxima_detection(
            envelope, prah_int[:, 1], fs, polyspike_union_time, ti_switch, d_decim
        )
        markers_low = _detection_union(markers_low, envelope, polyspike_union_time * fs)

    return envelope, markers_high, markers_low, prah_int, envelope_cdf, envelope_pdf


def _local_maxima_detection(
    envelope: np.ndarray,
    threshold: np.ndarray,
    fs: float,
    polyspike_union_time: float,
    ti_switch: int,
    d_decim: np.ndarray,
) -> np.ndarray:
    crossed = envelope > threshold
    sections = _true_runs(crossed)
    target = np.abs(d_decim) if ti_switch == 2 else envelope
    marker = np.zeros(envelope.shape, dtype=bool)

    for start, stop in sections:
        seg = target[start:stop]
        if seg.size == 0:
            continue
        # MATLAB tests point_end - point_start > 2 with inclusive endpoints,
        # which means the crossed section must contain at least 4 samples.
        if stop - start > 3:
            grad_sign = np.sign(np.diff(seg))
            maxima = np.flatnonzero(np.diff(np.r_[0, grad_sign]) < 0)
            marker[start + maxima] = True
        else:
            marker[start + int(np.argmax(seg))] = True

    pointer = np.flatnonzero(marker)
    if pointer.size == 0:
        return marker

    state_previous = False
    start_group = 0
    horizon = int(math.ceil(polyspike_union_time * fs))
    for p in pointer:
        seg_stop = min(p + horizon + 1, marker.size)
        has_next = np.any(marker[p + 1 : seg_stop])
        if state_previous:
            if not has_next:
                state_previous = False
                marker[start_group : p + 1] = True
        elif has_next:
            state_previous = True
            start_group = p

    for start, stop in _true_runs(marker):
        if stop - start <= 1:
            continue
        local_max = pointer[(pointer >= start) & (pointer < stop)]
        marker[start:stop] = False
        if local_max.size == 0:
            continue
        vals = target[local_max]
        falling = (np.sign(np.diff(np.r_[0, vals, 0])) < 0).astype(int)
        keep = np.diff(falling) > 0
        marker[local_max[keep]] = True

    return marker


def _detection_union(marker: np.ndarray, envelope: np.ndarray, union_samples: float) -> np.ndarray:
    union_samples = int(math.ceil(union_samples))
    if union_samples % 2 == 0:
        union_samples += 1
    mask = np.ones(union_samples, dtype=int)
    dilated = signal.convolve(marker.astype(int), mask, mode="same") > 0
    eroded = ~(signal.convolve((~dilated).astype(int), mask, mode="same") > 0)

    marker2 = np.zeros_like(marker, dtype=bool)
    for start, stop in _true_runs(eroded):
        if stop <= start:
            continue
        marker2[start + int(np.argmax(envelope[start:stop]))] = True
    return marker2


def _make_out(
    markers_high: np.ndarray,
    markers_low: np.ndarray,
    envelope_cdf: np.ndarray,
    envelope_pdf: np.ndarray,
    fs: float,
    cfg: DetectorSettings,
) -> DetectorOutput:
    pos = []
    dur = []
    chan = []
    con = []
    weight = []
    pdf = []
    t_dur = 0.005

    for ch in range(markers_high.shape[1]):
        idx = np.flatnonzero(markers_high[:, ch])
        if idx.size:
            pos.extend((idx + 1) / fs)
            dur.extend([t_dur] * idx.size)
            chan.extend([ch + 1] * idx.size)
            con.extend([1.0] * idx.size)
            weight.extend(envelope_cdf[idx, ch])
            pdf.extend(envelope_pdf[idx, ch])

    if cfg.k2 != cfg.k1:
        obvious_any = np.sum(markers_high, axis=1) > 0
        for ch in range(markers_low.shape[1]):
            idx = np.flatnonzero(markers_low[:, ch])
            idx = idx[~markers_high[idx, ch]]
            for i in idx:
                center = int(round(i - 0.01 * fs))
                if 0 <= center < obvious_any.size and obvious_any[center]:
                    pos.append((i + 1) / fs)
                    dur.append(t_dur)
                    chan.append(ch + 1)
                    con.append(0.5)
                    weight.append(envelope_cdf[i, ch])
                    pdf.append(envelope_pdf[i, ch])

    order = np.argsort(pos) if pos else np.array([], dtype=int)
    return DetectorOutput(
        pos=np.asarray(pos, dtype=float)[order],
        dur=np.asarray(dur, dtype=float)[order],
        chan=np.asarray(chan, dtype=int)[order],
        con=np.asarray(con, dtype=float)[order],
        weight=np.asarray(weight, dtype=float)[order],
        pdf=np.asarray(pdf, dtype=float)[order],
    )


def _make_discharges(
    out: DetectorOutput,
    d_band: np.ndarray,
    d_decim: np.ndarray,
    envelope: np.ndarray,
    background: np.ndarray,
    envelope_cdf: np.ndarray,
    envelope_pdf: np.ndarray,
    fs: float,
    cfg: DetectorSettings,
) -> Discharges:
    n_samples, n_channels = d_band.shape
    m = np.zeros((n_samples, n_channels), dtype=float)

    for pos, ch, condition in zip(out.pos, out.chan, out.con):
        start = int(round(pos * fs)) - 1
        stop = min(n_samples, int(round(pos * fs + cfg.discharge_tol * fs)))
        if 0 <= start < n_samples:
            m[start:stop, int(ch) - 1] = condition

    events = _true_runs(np.sum(m, axis=1) > 0)
    if not events:
        empty = np.empty((0, n_channels), dtype=float)
        return Discharges(empty, empty, empty, empty, empty, empty, empty)

    rows = []
    for start, stop in events:
        seg_m = m[start:stop, :]
        mv = np.max(seg_m, axis=0)

        if cfg.ti_switch == 2:
            pad = int(round(fs * 10e-3))
            a = max(0, start - pad)
            b = min(n_samples, stop + pad)
        else:
            a, b = start, stop

        ma = np.max(np.abs(envelope[a:b, :] - (background[a:b, :, 0] / cfg.k1)), axis=0)
        raw_seg = d_decim[start:stop, :]
        if raw_seg.size:
            raw_idx = np.argmax(np.abs(raw_seg), axis=0)
            mraw = raw_seg[raw_idx, np.arange(n_channels)]
        else:
            mraw = np.zeros(n_channels)

        mw = np.max(envelope_cdf[start:stop, :], axis=0)
        mpdf = np.max(envelope_pdf[start:stop, :] * (seg_m > 0), axis=0)
        mp = np.full(n_channels, np.nan, dtype=float)
        active_rows, active_cols = np.nonzero(seg_m > 0)
        if active_cols.size:
            for col in np.unique(active_cols):
                mp[col] = (active_rows[active_cols == col][0] + start + 1) / fs

        md = np.full(n_channels, (stop - start) / fs, dtype=float)
        rows.append((mraw, mv, ma, mp, md, mw, mpdf))

    return Discharges(
        MRAW=np.vstack([r[0] for r in rows]),
        MV=np.vstack([r[1] for r in rows]),
        MA=np.vstack([r[2] for r in rows]),
        MP=np.vstack([r[3] for r in rows]),
        MD=np.vstack([r[4] for r in rows]),
        MW=np.vstack([r[5] for r in rows]),
        MPDF=np.vstack([r[6] for r in rows]),
    )


def _filtering(d: np.ndarray, fs: float, cfg: DetectorSettings) -> np.ndarray:
    f_type = cfg.filter_type
    if cfg.decimation != 200 and f_type == 1:
        f_type = 2

    low, high = cfg.bandwidth
    if f_type == 1:
        wp = 2 * high / fs
        ws = min(1.0, 2 * high / fs + 0.1)
        n, _ = signal.cheb2ord(wp, ws, 6, 60)
        bl, al = signal.cheby2(n, 60, ws)

        wp = 2 * low / fs
        ws = max(1e-5, 2 * low / fs - 0.05)
        n, _ = signal.cheb2ord(wp, ws, 6, 60)
        bh, ah = signal.cheby2(n, 60, ws, btype="high")
    elif f_type == 2:
        wp = 2 * high / fs
        ws = min(1.0, 2 * high / fs + 0.1)
        n, ws = signal.buttord(wp, ws, 6, 60)
        bl, al = signal.butter(n, ws)

        wp = 2 * low / fs
        ws = max(0.1, 2 * low / fs - 0.05)
        n, ws = signal.buttord(wp, ws, 6, 60)
        bh, ah = signal.butter(n, ws, btype="high")
    elif f_type == 3:
        taps = int(fs // 2)
        if taps % 2 == 0:
            taps += 1
        bl, al = signal.firwin(taps, 2 * high / fs), [1.0]
        bh, ah = signal.firwin(taps, 2 * low / fs, pass_zero=False), [1.0]
    else:
        raise ValueError(f"unknown filter type: {cfg.filter_type}")

    out = _filtfilt(bh, ah, d)
    if high == fs / 2:
        return out
    return _filtfilt(bl, al, out)


def _filt_hum(d: np.ndarray, fs: float, hum_fs: float, bandwidth: Tuple[float, float]) -> np.ndarray:
    out = d.copy()
    f0 = np.arange(hum_fs, fs / 2 + 1e-9, hum_fs)
    f0 = f0[f0 <= 1.1 * bandwidth[1]]
    r = 0.985
    for freq in f0:
        b = np.array([1.0, -2 * np.cos(2 * np.pi * freq / fs), 1.0])
        a = np.array([1.0, -2 * r * np.cos(2 * np.pi * freq / fs), r * r])
        out = _filtfilt(b, a, out)
    return out


def _beta_detect(d: np.ndarray, fs: float, beta: float, winsize_sec: float, beta_ar: int) -> np.ndarray:
    # Optional branch in the MATLAB code.  Kept conservative because the
    # spike-gamma example leaves beta detection disabled with beta=Inf.
    winsize = int(round(winsize_sec * fs))
    noverlap = int(round(0.5 * winsize))
    step = max(1, winsize - noverlap)
    if winsize <= 0 or d.shape[0] < winsize:
        return np.zeros_like(d, dtype=bool)

    index = np.arange(0, d.shape[0] - winsize + 1, step, dtype=int)
    b, a = signal.butter(4, 2 * 30 / fs)
    mask = np.zeros_like(d, dtype=bool)
    for ch in range(d.shape[1]):
        flags = []
        for start in index:
            seg = _filtfilt(b, a, d[start : start + winsize, ch])
            # Python first pass: approximate MATLAB lpc/freqz beta exclusion
            # with Welch peak detection in the beta-25 Hz band.
            freqs, power = signal.welch(seg - np.mean(seg), fs=fs, nperseg=min(len(seg), 512))
            band = (freqs > beta) & (freqs < 25)
            flags.append(bool(np.any(band) and np.max(power[band]) > np.median(power) * beta_ar))
        if flags:
            nearest = interp1d(
                np.r_[index, d.shape[0] - 1],
                np.r_[flags, flags[-1]],
                kind="nearest",
                bounds_error=False,
                fill_value=(flags[0], flags[-1]),
            )
            mask[:, ch] = nearest(np.arange(d.shape[0])).astype(bool)
    return mask


def _interpolate_phat(phat: np.ndarray, index: np.ndarray, winsize: int, n: int) -> np.ndarray:
    if phat.shape[0] <= 1:
        return np.tile(phat[0], (n, 1))

    centers = index + int(round(winsize / 2))
    x_new = np.arange(index[0], index[-1] + 1) + int(round(winsize / 2))
    if phat.shape[0] >= 4:
        mu = CubicSpline(centers, phat[:, 0], bc_type="not-a-knot", extrapolate=True)(x_new)
        sigma = CubicSpline(centers, phat[:, 1], bc_type="not-a-knot", extrapolate=True)(x_new)
    else:
        mu = interp1d(centers, phat[:, 0], kind="linear", fill_value="extrapolate")(x_new)
        sigma = interp1d(centers, phat[:, 1], kind="linear", fill_value="extrapolate")(x_new)
    mid = np.column_stack([mu, sigma])

    pre_len = int(math.floor(winsize / 2))
    post_len = n - (mid.shape[0] + pre_len)
    pre = np.tile(mid[0], (max(0, pre_len), 1))
    post = np.tile(mid[-1], (max(0, post_len), 1))
    out = np.vstack([pre, mid, post])
    if out.shape[0] < n:
        out = np.vstack([out, np.tile(out[-1], (n - out.shape[0], 1))])
    return out[:n, :]


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    mask = np.asarray(mask, dtype=bool).ravel()
    edges = np.diff(np.r_[False, mask, False].astype(int))
    starts = np.flatnonzero(edges == 1)
    stops = np.flatnonzero(edges == -1)
    return list(zip(starts, stops))


def _resample_columns(d: np.ndarray, fs_out: float, fs: float) -> np.ndarray:
    if np.isclose(fs_out, round(fs_out), rtol=0.0, atol=1e-9) and np.isclose(fs, round(fs), rtol=0.0, atol=1e-9):
        up = int(round(fs_out))
        down = int(round(fs))
    else:
        ratio = Fraction(float(fs_out) / float(fs)).limit_denominator(1_000_000)
        up = ratio.numerator
        down = ratio.denominator
    gcd = math.gcd(up, down)
    up //= gcd
    down //= gcd
    max_rate = max(up, down)
    half_len = 100 * max_rate
    window = signal.firwin(
        2 * half_len + 1,
        1.0 / max_rate,
        window=("kaiser", 5.0),
    )
    return signal.resample_poly(d, up, down, axis=0, window=window)


def _filtfilt(b: np.ndarray, a: np.ndarray, x: np.ndarray) -> np.ndarray:
    axis = 0 if np.ndim(x) > 1 else -1
    padlen = 3 * (max(len(np.ravel(a)), len(np.ravel(b))) - 1)
    return signal.filtfilt(b, a, x, axis=axis, padlen=padlen)


def _as_2d(data: np.ndarray) -> np.ndarray:
    arr = np.asarray(data, dtype=float)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.ndim != 2:
        raise ValueError("data must be a 1D or 2D array shaped samples x channels")
    return arr


def _filter_out(out: DetectorOutput, keep: np.ndarray) -> DetectorOutput:
    return DetectorOutput(
        pos=out.pos[keep],
        dur=out.dur[keep],
        chan=out.chan[keep],
        con=out.con[keep],
        weight=out.weight[keep],
        pdf=out.pdf[keep],
    )


def _filter_discharges(d: Discharges, keep: np.ndarray) -> Discharges:
    return Discharges(
        MV=d.MV[keep, :],
        MA=d.MA[keep, :],
        MP=d.MP[keep, :],
        MD=d.MD[keep, :],
        MW=d.MW[keep, :],
        MPDF=d.MPDF[keep, :],
        MRAW=d.MRAW[keep, :],
    )


__all__ = [
    "DetectorOutput",
    "DetectorSettings",
    "Discharges",
    "parse_settings",
    "spike_detector_hilbert_v25",
]
