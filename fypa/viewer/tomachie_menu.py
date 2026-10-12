"""File > Load Tomachie pdn.json… : set up the whole PDN from a Tomachie power-tree file.

Tomachie reads the schematic and the parts' datasheets and writes ``<design>.pdn.json``:
sources, regulators, series parts and every load's current and minimum supply voltage.
This action takes that file and the Altium project it describes, and opens the project
with the file's directives (see :mod:`fypa.tomachie_pdn`). The choice rides on the window
(and its ``.fypa`` project) so every later Resolve / Re-run keeps it.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

CORNERS = {"Typical currents and resistances": "typ",
           "Worst case (maximum currents and resistances)": "max"}


def pdn_choice(window) -> tuple[Path | None, str]:
    """The pdn.json (and corner) the next solve on ``window`` loads, if any."""
    chosen = getattr(window, "_tomachie_pdn", None)
    if chosen:
        return chosen
    project = getattr(window, "_project", None)
    path = getattr(project, "pdn_json", None)
    return (Path(path) if path else None), getattr(project, "pdn_corner", "typ") or "typ"


def _design_name(pdn_path: Path) -> str:
    try:
        return json.loads(pdn_path.read_text(encoding="utf-8")).get("design", "")
    except (OSError, ValueError):
        return ""


def on_load_tomachie_pdn(window) -> None:
    from fypa.viewer.project_open import _open_altium_project_at

    pdn_str, _ = QFileDialog.getOpenFileName(
        window, "Load Tomachie pdn.json", "",
        "Tomachie power tree (*.pdn.json);;JSON (*.json)")
    if not pdn_str:
        return
    pdn_path = Path(pdn_str)
    design = _design_name(pdn_path)
    label, ok = QInputDialog.getItem(
        window, "Load Tomachie pdn.json", "Solve with:", list(CORNERS), 0, False)
    if not ok:
        return
    prj_str, _ = QFileDialog.getOpenFileName(
        window, f"Altium project for {design}" if design else "Altium project",
        str(pdn_path.parent.parent), "Altium project (*.PrjPcb);;All files (*)")
    if not prj_str:
        return
    corner = CORNERS[label]
    window._tomachie_pdn = (pdn_path, corner)
    project = getattr(window, "_project", None)
    if project is not None and hasattr(project, "pdn_json"):
        project.pdn_json, project.pdn_corner = pdn_path, corner
    QMessageBox.information(
        window, "Load Tomachie pdn.json",
        f"{pdn_path.name} sets up sources, regulators, series parts and loads for every "
        "component the schematic does not annotate itself. What it cannot place is "
        "listed in the Setup tab's warnings.")
    _open_altium_project_at(window, Path(prj_str), None, clean=False, force_solve=True)
