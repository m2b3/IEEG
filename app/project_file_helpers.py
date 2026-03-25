from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget


class ProjectFileHelper:
    EEG_FILE_FILTER = "EEG files (*.edf *.bdf *.fif *.vhdr *.set *.cnt *.eeg *.mff);;All files (*)"
    PROJECT_FILE_FILTER = "iEEG Project (*.ieeg);;All files (*)"

    @staticmethod
    def choose_raw_file(parent: QWidget) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(
            parent,
            "Open EEG/iEEG file",
            "",
            ProjectFileHelper.EEG_FILE_FILTER,
        )
        return Path(path) if path else None

    @staticmethod
    def choose_project_to_open(parent: QWidget) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(
            parent,
            "Open project",
            "",
            ProjectFileHelper.PROJECT_FILE_FILTER,
        )
        return Path(path) if path else None

    @staticmethod
    def choose_project_to_create(parent: QWidget, raw_path: Path) -> Path | None:
        project_default = str(raw_path.with_suffix("")) + ".ieeg"
        path, _ = QFileDialog.getSaveFileName(
            parent,
            "Create project file",
            project_default,
            ProjectFileHelper.PROJECT_FILE_FILTER,
        )
        if not path:
            return None

        project_path = Path(path)
        if project_path.suffix.lower() != ".ieeg":
            project_path = project_path.with_suffix(".ieeg")
        return project_path

    @staticmethod
    def choose_project_to_save_as(parent: QWidget, default: str) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(
            parent,
            "Save project as",
            default,
            ProjectFileHelper.PROJECT_FILE_FILTER,
        )
        if not path:
            return None

        project_path = Path(path)
        if project_path.suffix.lower() != ".ieeg":
            project_path = project_path.with_suffix(".ieeg")
        return project_path

    @staticmethod
    def resolve_project_raw_path(parent: QWidget, project_path: Path, source: dict) -> Path | None:
        raw_file = source.get("raw_file")
        raw_file_relative = source.get("raw_file_relative")

        if isinstance(raw_file, str) and raw_file.strip():
            abs_path = Path(raw_file)
            if abs_path.exists():
                return abs_path

        if isinstance(raw_file_relative, str) and raw_file_relative.strip():
            rel_path = project_path.parent / raw_file_relative
            if rel_path.exists():
                return rel_path

        start_dir = str(project_path.parent)
        located_path, _ = QFileDialog.getOpenFileName(
            parent,
            "Locate raw EEG file",
            start_dir,
            ProjectFileHelper.EEG_FILE_FILTER,
        )
        if not located_path:
            QMessageBox.critical(
                parent,
                "Open project error",
                "Raw EEG file could not be located.",
            )
            return None

        raw_path = Path(located_path)
        if not raw_path.exists():
            QMessageBox.critical(
                parent,
                "Open project error",
                f"Raw EEG file not found:\n{raw_path}",
            )
            return None

        return raw_path
