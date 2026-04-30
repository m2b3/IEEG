# app/annotations.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple, Dict
import uuid


# Your agreed mapping:
# | Spike | blue |
# | Ripple | yellow |
# | Fast ripple | orange |
# | Artifact | purple |
# | Bad segment | red |
# | Other | green |

ANNOTATION_STYLES: Dict[str, Tuple[int, int, int]] = {
    "Spike": (0, 128, 255),        # blue
    "Ripple": (255, 255, 0),       # yellow
    "Fast ripple": (255, 165, 0),  # orange
    "Artifact": (160, 32, 240),    # purple
    "Bad segment": (255, 0, 0),    # red
    "Other": (0, 200, 0),          # green
}

ANNOTATION_TYPES = list(ANNOTATION_STYLES.keys())

SCOPE_CLICKED = "Clicked channel"
SCOPE_SELECTED = "Selected channels"
SCOPE_GLOBAL = "Global (all channels)"
ANNOTATION_SCOPES = [SCOPE_CLICKED, SCOPE_SELECTED, SCOPE_GLOBAL]


def new_id() -> str:
    return uuid.uuid4().hex

@dataclass(frozen=True)
class Annotation:
    """Minimal annotation object for GUI overlay and later export."""
    id: str
    kind: str                # e.g. "Spike", "Ripple", ...
    t_start: float           # seconds
    t_end: float             # seconds
    abs_channel: Optional[int]        # index in displayed channel list (your abs indices)
    note: str = ""
