from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
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
    Non-destructive:
    returns a filtered copy, never mutates source_raw.
    Applies macro settings only to macro channels and micro settings only to micro channels.
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