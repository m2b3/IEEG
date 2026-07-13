from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
from scipy import signal
from mne.io import BaseRaw


NOTCH_OFF = "Off"
NOTCH_50_HARM = "50 Hz + harmonics"
NOTCH_60_HARM = "60 Hz + harmonics"


@dataclass
class FilterSettings:
    highpass_hz: float | None = None
    lowpass_hz: float | None = None
    notch_mode: str = NOTCH_OFF


@dataclass
class FilterProfiles:
    macro: FilterSettings = field(default_factory=FilterSettings)
    micro: FilterSettings = field(default_factory=FilterSettings)


def _clean_cutoff(value: float | None) -> float | None:
    if value is None:
        return None
    v = float(value)
    if v <= 0.0:
        return None
    return v


def validate_filter_settings(settings: FilterSettings, sfreq: float) -> tuple[bool, str]:
    hp = _clean_cutoff(settings.highpass_hz)
    lp = _clean_cutoff(settings.lowpass_hz)

    nyquist = 0.5 * float(sfreq)

    if hp is not None and hp >= nyquist:
        return False, f"High-pass must be below Nyquist ({nyquist:.3f} Hz)."

    if lp is not None and lp >= nyquist:
        return False, f"Low-pass must be below Nyquist ({nyquist:.3f} Hz)."

    if hp is not None and lp is not None and hp >= lp:
        return False, "High-pass must be lower than low-pass."

    if settings.notch_mode not in {NOTCH_OFF, NOTCH_50_HARM, NOTCH_60_HARM}:
        return False, "Invalid notch mode."

    return True, ""


def is_filter_active(settings: FilterSettings) -> bool:
    return (
        _clean_cutoff(settings.highpass_hz) is not None
        or _clean_cutoff(settings.lowpass_hz) is not None
        or settings.notch_mode != NOTCH_OFF
    )


def _harmonic_freqs(base_hz: float, sfreq: float) -> np.ndarray:
    nyquist = 0.5 * float(sfreq)
    freqs = np.arange(base_hz, nyquist, base_hz, dtype=float)
    return freqs[freqs > 0.0]


def filter_signature(settings: FilterSettings) -> tuple[float | None, float | None, str]:
    return (
        _clean_cutoff(settings.highpass_hz),
        _clean_cutoff(settings.lowpass_hz),
        str(settings.notch_mode),
    )


def profiles_signature(profiles: FilterProfiles) -> tuple:
    return (
        filter_signature(profiles.macro),
        filter_signature(profiles.micro),
    )


def filter_padding_seconds(settings: FilterSettings) -> float:
    if not is_filter_active(settings):
        return 0.0

    hp = _clean_cutoff(settings.highpass_hz)
    lp = _clean_cutoff(settings.lowpass_hz)
    padding_s = 2.0
    if hp is not None:
        padding_s = max(padding_s, 3.0 / max(hp, 0.1))
    if lp is not None:
        padding_s = max(padding_s, min(5.0, 3.0 / max(lp, 0.1)))
    if settings.notch_mode != NOTCH_OFF:
        padding_s = max(padding_s, 2.0)
    return float(min(30.0, padding_s))


def profiles_padding_seconds(profiles: FilterProfiles) -> float:
    return max(
        filter_padding_seconds(profiles.macro),
        filter_padding_seconds(profiles.micro),
    )


def apply_settings_to_array(
    data: np.ndarray,
    sfreq: float,
    settings: FilterSettings,
) -> np.ndarray:
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or arr.size == 0 or not is_filter_active(settings):
        return arr

    hp = _clean_cutoff(settings.highpass_hz)
    lp = _clean_cutoff(settings.lowpass_hz)
    filtered = np.asarray(arr, dtype=np.float64)
    nyquist = 0.5 * float(sfreq)

    if hp is not None or lp is not None:
        if hp is not None and lp is not None:
            low = max(1e-6, float(hp))
            high = min(float(lp), nyquist - 1e-6)
            sos = signal.butter(
                N=4,
                Wn=[low, high],
                btype="bandpass",
                fs=float(sfreq),
                output="sos",
            )
        elif hp is not None:
            sos = signal.butter(
                N=4,
                Wn=max(1e-6, float(hp)),
                btype="highpass",
                fs=float(sfreq),
                output="sos",
            )
        else:
            sos = signal.butter(
                N=4,
                Wn=min(float(lp), nyquist - 1e-6),
                btype="lowpass",
                fs=float(sfreq),
                output="sos",
            )
        filtered = signal.sosfiltfilt(sos, filtered, axis=1)

    if settings.notch_mode == NOTCH_50_HARM:
        freqs = _harmonic_freqs(50.0, float(sfreq))
    elif settings.notch_mode == NOTCH_60_HARM:
        freqs = _harmonic_freqs(60.0, float(sfreq))
    else:
        freqs = np.asarray([], dtype=float)

    for freq in freqs:
        if float(freq) >= nyquist:
            continue
        b, a = signal.iirnotch(w0=float(freq), Q=30.0, fs=float(sfreq))
        sos = signal.tf2sos(b, a)
        filtered = signal.sosfiltfilt(sos, filtered, axis=1)

    return np.asarray(filtered, dtype=float)


def _apply_settings_to_picks(raw: BaseRaw, settings: FilterSettings, picks) -> None:
    hp = _clean_cutoff(settings.highpass_hz)
    lp = _clean_cutoff(settings.lowpass_hz)
    sfreq = float(raw.info["sfreq"])

    if not picks:
        return

    if hp is not None or lp is not None:
        raw.filter(
            l_freq=hp,
            h_freq=lp,
            picks=picks,
            method="fir",
            phase="zero",
            verbose="ERROR",
        )

    if settings.notch_mode == NOTCH_50_HARM:
        freqs = _harmonic_freqs(50.0, sfreq)
        if freqs.size:
            raw.notch_filter(freqs=freqs, picks=picks, verbose="ERROR")
    elif settings.notch_mode == NOTCH_60_HARM:
        freqs = _harmonic_freqs(60.0, sfreq)
        if freqs.size:
            raw.notch_filter(freqs=freqs, picks=picks, verbose="ERROR")


def build_filtered_raw_by_group(
    source_raw: BaseRaw,
    profiles: FilterProfiles,
    channel_groups: dict[str, str],
) -> BaseRaw:
    """
    Full-file filtering helper for explicit export/permanent workflows only.
    Normal browsing should use windowed display filtering instead.

    Non-destructive: returns a filtered copy, never mutates source_raw.
    Applies macro settings only to macro channels and micro settings only to micro channels.

    Callers should warn the user before invoking this on large recordings because
    it loads and filters the full file in memory.
    """
    raw = source_raw.copy().load_data()

    macro_picks = []
    micro_picks = []

    for idx, ch_name in enumerate(raw.ch_names):
        group = str(channel_groups.get(str(ch_name), "macro")).lower()
        if group == "micro":
            micro_picks.append(idx)
        else:
            macro_picks.append(idx)

    _apply_settings_to_picks(raw, profiles.macro, macro_picks)
    _apply_settings_to_picks(raw, profiles.micro, micro_picks)

    return raw
