from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DisplayTheme:
    key: str
    viewer_background: str
    window_background: str
    panel_background: str
    border_color: str
    text_color: str
    secondary_text_color: str
    input_background: str
    input_text_color: str
    button_background: str
    button_hover_background: str
    micro_trace_color: tuple[int, int, int]
    micro_label_color: tuple[int, int, int]
    macro_trace_color: tuple[int, int, int]
    macro_label_color: tuple[int, int, int]
    selected_label_color: tuple[int, int, int]
    axis_color: tuple[int, int, int]
    time_grid_color: tuple[int, int, int]
    cursor_color: tuple[int, int, int]
    minmax_text_color: tuple[int, int, int]
    annotation_text_color: tuple[int, int, int]
    preview_outline_color: tuple[int, int, int]
    raw_signal_color: tuple[int, int, int]


DISPLAY_THEMES: dict[str, DisplayTheme] = {
    "dark": DisplayTheme(
        key="dark",
        viewer_background="#000000",
        window_background="#1b1b1b",
        panel_background="#2b2b2b",
        border_color="#444444",
        text_color="#dddddd",
        secondary_text_color="#bbbbbb",
        input_background="#323232",
        input_text_color="#f2f2f2",
        button_background="#3a3a3a",
        button_hover_background="#4a4a4a",
        micro_trace_color=(79, 195, 247),
        micro_label_color=(79, 195, 247),
        macro_trace_color=(255, 255, 255),
        macro_label_color=(180, 180, 180),
        selected_label_color=(255, 230, 64),
        axis_color=(210, 210, 210),
        time_grid_color=(90, 90, 90),
        cursor_color=(255, 230, 64),
        minmax_text_color=(160, 160, 160),
        annotation_text_color=(255, 255, 255),
        preview_outline_color=(255, 255, 255),
        raw_signal_color=(230, 230, 230),
    ),
    "light": DisplayTheme(
        key="light",
        viewer_background="#ffffff",
        window_background="#f7f7f7",
        panel_background="#efefef",
        border_color="#cfcfcf",
        text_color="#222222",
        secondary_text_color="#555555",
        input_background="#ffffff",
        input_text_color="#1f1f1f",
        button_background="#e3e3e3",
        button_hover_background="#d5d5d5",
        micro_trace_color=(0, 102, 204),
        micro_label_color=(0, 102, 204),
        macro_trace_color=(32, 32, 32),
        macro_label_color=(90, 90, 90),
        selected_label_color=(176, 95, 0),
        axis_color=(60, 60, 60),
        time_grid_color=(210, 210, 210),
        cursor_color=(176, 95, 0),
        minmax_text_color=(110, 110, 110),
        annotation_text_color=(25, 25, 25),
        preview_outline_color=(40, 40, 40),
        raw_signal_color=(40, 40, 40),
    ),
}

DISPLAY_THEME_CHOICES: tuple[tuple[str, str], ...] = (
    ("Dark", "dark"),
    ("Light", "light"),
)


def get_display_theme(theme_key: str) -> DisplayTheme:
    return DISPLAY_THEMES.get(str(theme_key).strip().lower(), DISPLAY_THEMES["dark"])
