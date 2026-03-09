from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Iterable


_CORE_CONTACT_RE = re.compile(r"([A-Z][A-Z0-9']*?)\s*0*([0-9]+)")
_TRAILING_REF_RE = re.compile(r"[-\s]*G\d+\s*$")


@dataclass(frozen=True)
class ParsedChannel:
    original_label: str
    normalized_label: str
    electrode_prefix: str
    contact_number: int


@dataclass(frozen=True)
class BipolarPair:
    name: str
    ch1: str
    ch2: str
    origin: str = "auto"


@dataclass(frozen=True)
class BipolarMontage:
    pairs: list[BipolarPair]
    unparsed_channels: list[str]
    non_consecutive_channels: list[str]
    bad_channel_skips: list[str]

    @property
    def skipped_channels(self) -> list[str]:
        merged = (
            self.unparsed_channels
            + self.non_consecutive_channels
            + self.bad_channel_skips
        )
        seen: set[str] = set()
        out: list[str] = []
        for ch in merged:
            if ch not in seen:
                seen.add(ch)
                out.append(ch)
        return out


def normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip()).upper()


def extract_core_contact_label(label: str) -> str | None:
    """
    Try to extract the actual electrode contact label from acquisition-style names.

    Examples:
      EEG RAI1-G2    -> RAI1
      EEG RAI 2 -G2  -> RAI2
      EEG RPI6 G2    -> RPI6
      RAI1           -> RAI1
    """
    text = normalize_label(label)

    # Remove common leading acquisition prefix
    if text.startswith("EEG "):
        text = text[4:].strip()

    # Remove common trailing reference suffix like -G2 or G2
    text = _TRAILING_REF_RE.sub("", text).strip()

    # Remove all remaining spaces so "RAI 2" becomes "RAI2"
    compact = re.sub(r"\s+", "", text)

    match = _CORE_CONTACT_RE.search(compact)
    if not match:
        return None

    prefix = match.group(1)
    number = int(match.group(2))
    return f"{prefix}{number}"


def parse_channel_label(label: str) -> ParsedChannel | None:
    core = extract_core_contact_label(label)
    if core is None:
        return None

    match = re.match(r"^([A-Z][A-Z0-9']*?)([0-9]+)$", core)
    if not match:
        return None

    prefix = match.group(1)
    number = int(match.group(2))

    return ParsedChannel(
        original_label=label,
        normalized_label=core,
        electrode_prefix=prefix,
        contact_number=number,
    )


def build_automatic_bipolar_montage(
    channel_labels: Iterable[str],
    bad_channels: Iterable[str] | None = None,
) -> BipolarMontage:
    bad_set = set()
    for ch in (bad_channels or []):
        parsed_bad = parse_channel_label(ch)
        if parsed_bad is not None:
            bad_set.add(parsed_bad.normalized_label)
        else:
            bad_set.add(normalize_label(ch))

    grouped: dict[str, list[ParsedChannel]] = {}
    unparsed_channels: list[str] = []
    non_consecutive_channels: list[str] = []
    bad_channel_skips: list[str] = []

    for label in channel_labels:
        parsed = parse_channel_label(label)
        if parsed is None:
            unparsed_channels.append(label)
            continue
        grouped.setdefault(parsed.electrode_prefix, []).append(parsed)

    pairs: list[BipolarPair] = []

    for group_channels in grouped.values():
        group_channels.sort(key=lambda ch: ch.contact_number)

        for left, right in zip(group_channels, group_channels[1:]):
            if right.contact_number != left.contact_number + 1:
                non_consecutive_channels.extend([left.original_label, right.original_label])
                continue

            if left.normalized_label in bad_set or right.normalized_label in bad_set:
                bad_channel_skips.extend([left.original_label, right.original_label])
                continue

            pairs.append(
                BipolarPair(
                    name=f"{left.normalized_label}-{right.normalized_label}",
                    ch1=left.original_label,
                    ch2=right.original_label,
                    origin="auto",
                )
            )

    return BipolarMontage(
        pairs=pairs,
        unparsed_channels=_unique_keep_order(unparsed_channels),
        non_consecutive_channels=_unique_keep_order(non_consecutive_channels),
        bad_channel_skips=_unique_keep_order(bad_channel_skips),
    )


def update_pair_channel2(pair: BipolarPair, new_ch2: str) -> BipolarPair:
    new_ch2_core = extract_core_contact_label(new_ch2) or new_ch2
    ch1_core = extract_core_contact_label(pair.ch1) or pair.ch1
    return replace(
        pair,
        ch2=new_ch2,
        name=f"{ch1_core}-{new_ch2_core}",
        origin="manual",
    )


def _unique_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out