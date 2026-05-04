from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Iterable


_CORE_CONTACT_RE = re.compile(r"([A-Z][A-Z0-9']*?)\s*0*([0-9]+)")
_TRAILING_REF_RE = re.compile(r"[-\s]*G\d+\s*$")
_BIPOLAR_SEPARATOR_RE = re.compile("\\s*(?:-|\\u2013|\\u2014)\\s*")


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


def looks_like_bipolar_derivation_label(label: str) -> bool:
    """Return True for labels that already look like contact-to-contact derivations."""
    text = normalize_label(label)
    if text.startswith("EEG "):
        text = text[4:].strip()

    # Acquisition labels such as EEG RAI1-G2 are monopolar channels with a
    # reference suffix in this project, not a bipolar derivation.
    if _TRAILING_REF_RE.search(text):
        return False

    parts = [part.strip() for part in _BIPOLAR_SEPARATOR_RE.split(text) if part.strip()]
    if len(parts) != 2:
        return False

    left = parse_channel_label(parts[0])
    right = parse_channel_label(parts[1])
    return left is not None and right is not None


def bipolar_pair_display_name(ch1: str, ch2: str) -> str:
    """Build the displayed label for a bipolar derivation."""
    ch1_text = str(ch1).strip()
    ch2_text = str(ch2).strip()

    if (
        looks_like_bipolar_derivation_label(ch1_text)
        or looks_like_bipolar_derivation_label(ch2_text)
    ):
        return f"({ch1_text})-({ch2_text})"

    ch1_core = extract_core_contact_label(ch1_text) or ch1_text
    ch2_core = extract_core_contact_label(ch2_text) or ch2_text
    return f"{ch1_core}-{ch2_core}"


def refresh_bipolar_montage_pair_names(montage: BipolarMontage) -> BipolarMontage:
    """Return a montage with pair names regenerated from their source channels."""
    return replace(
        montage,
        pairs=[
            replace(pair, name=bipolar_pair_display_name(pair.ch1, pair.ch2))
            for pair in montage.pairs
        ],
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
                    name=bipolar_pair_display_name(left.original_label, right.original_label),
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
    return replace(
        pair,
        ch2=new_ch2,
        name=bipolar_pair_display_name(pair.ch1, new_ch2),
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
