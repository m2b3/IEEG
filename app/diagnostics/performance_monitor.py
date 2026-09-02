# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import csv
import ctypes
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import psutil
except Exception:  # pragma: no cover - optional diagnostic dependency
    psutil = None


CSV_COLUMNS = [
    "file_path",
    "duration_h",
    "n_channels",
    "sfreq",
    "step",
    "elapsed_s",
    "working_set_mb",
    "private_mb",
    "cpu_percent",
    "smoothness_score",
    "visible_window_s",
    "filter_mode",
    "reference_mode",
    "notes",
]


class PerformanceMonitor:
    """Temporary CSV logger for large-file performance testing."""

    def __init__(self) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.output_path = Path("reports") / "performance" / f"ieeg_perf_{stamp}.csv"
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path = ""
        self.duration_h = ""
        self.n_channels = ""
        self.sfreq = ""
        self._process = psutil.Process() if psutil is not None else None
        self._last_cpu_wall = time.perf_counter()
        self._last_cpu_seconds = self._process_cpu_seconds()
        if self._process is not None:
            try:
                self._process.cpu_percent(interval=None)
            except Exception:
                pass
        self._ensure_header()

    def _ensure_header(self) -> None:
        if self.output_path.exists() and self.output_path.stat().st_size > 0:
            return
        with self.output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()

    def set_file_context(self, file_path: Any, raw: Any | None = None) -> None:
        if file_path:
            self.file_path = str(file_path)
        elif raw is not None and not self.file_path:
            filenames = getattr(raw, "filenames", None)
            if filenames:
                first = next((name for name in filenames if name is not None), None)
                if first is not None:
                    self.file_path = str(first)
        if raw is None:
            return
        try:
            sfreq = float(raw.info["sfreq"])
            n_times = int(raw.n_times)
            self.duration_h = f"{(n_times / sfreq) / 3600.0:.6f}"
            self.n_channels = str(int(raw.info["nchan"]))
            self.sfreq = f"{sfreq:g}"
        except Exception:
            pass

    def _process_cpu_seconds(self) -> float:
        try:
            times = os.times()
            return float(times.user + times.system)
        except Exception:
            return 0.0

    def _fallback_cpu_percent(self) -> str:
        now_wall = time.perf_counter()
        now_cpu = self._process_cpu_seconds()
        delta_wall = now_wall - self._last_cpu_wall
        delta_cpu = now_cpu - self._last_cpu_seconds
        self._last_cpu_wall = now_wall
        self._last_cpu_seconds = now_cpu
        if delta_wall <= 0:
            return ""
        return f"{max(0.0, 100.0 * delta_cpu / delta_wall):.3f}"

    def _windows_memory_snapshot(self) -> tuple[str, str]:
        if sys.platform != "win32":
            return "", ""

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        try:
            kernel32 = ctypes.WinDLL("kernel32.dll")
            psapi = ctypes.WinDLL("psapi.dll")
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ProcessMemoryCountersEx),
                ctypes.c_ulong,
            ]
            psapi.GetProcessMemoryInfo.restype = ctypes.c_int

            counters = ProcessMemoryCountersEx()
            counters.cb = ctypes.sizeof(ProcessMemoryCountersEx)
            handle = kernel32.GetCurrentProcess()
            ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            if not ok:
                return "", ""
            mb = 1024.0 * 1024.0
            return (
                f"{float(counters.WorkingSetSize) / mb:.3f}",
                f"{float(counters.PrivateUsage) / mb:.3f}",
            )
        except Exception:
            return "", ""

    def memory_snapshot(self) -> tuple[str, str, str]:
        if self._process is None:
            working_set_mb, private_mb = self._windows_memory_snapshot()
            return working_set_mb, private_mb, self._fallback_cpu_percent()
        try:
            info = self._process.memory_info()
            full_info = None
            try:
                full_info = self._process.memory_full_info()
            except Exception:
                full_info = None
            working_set_mb = float(getattr(info, "rss", 0)) / (1024.0 * 1024.0)
            private_bytes = getattr(full_info, "private", None) if full_info is not None else None
            if private_bytes is None:
                private_bytes = getattr(info, "private", None)
            private_mb = (
                float(private_bytes) / (1024.0 * 1024.0)
                if private_bytes is not None
                else ""
            )
            cpu_percent = self._process.cpu_percent(interval=None)
            return (
                f"{working_set_mb:.3f}",
                f"{private_mb:.3f}" if private_mb != "" else "",
                f"{float(cpu_percent):.3f}",
            )
        except Exception:
            working_set_mb, private_mb = self._windows_memory_snapshot()
            return working_set_mb, private_mb, self._fallback_cpu_percent()

    def mark(
        self,
        step: str,
        *,
        elapsed_s: float | None = None,
        raw: Any | None = None,
        file_path: Any | None = None,
        visible_window_s: float | None = None,
        filter_mode: str = "",
        reference_mode: str = "",
        notes: str = "",
    ) -> None:
        if file_path is not None or raw is not None:
            self.set_file_context(file_path if file_path is not None else self.file_path, raw)

        working_set_mb, private_mb, cpu_percent = self.memory_snapshot()
        row = {
            "file_path": self.file_path,
            "duration_h": self.duration_h,
            "n_channels": self.n_channels,
            "sfreq": self.sfreq,
            "step": str(step),
            "elapsed_s": f"{float(elapsed_s):.6f}" if elapsed_s is not None else "",
            "working_set_mb": working_set_mb,
            "private_mb": private_mb,
            "cpu_percent": cpu_percent,
            "smoothness_score": "",
            "visible_window_s": (
                f"{float(visible_window_s):.6f}" if visible_window_s is not None else ""
            ),
            "filter_mode": str(filter_mode or ""),
            "reference_mode": str(reference_mode or ""),
            "notes": str(notes or ""),
        }
        with self.output_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writerow(row)


_MONITOR = PerformanceMonitor()


def monitor() -> PerformanceMonitor:
    return _MONITOR


def timed_mark(step: str, start_s: float, **kwargs: Any) -> None:
    monitor().mark(step, elapsed_s=time.perf_counter() - float(start_s), **kwargs)
