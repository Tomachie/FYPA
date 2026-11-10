"""The board temperature from a Tomachie file's maximum ambient (fypa.tomachie_pdn)."""

import copy
import json
from types import SimpleNamespace

import pytest

from fypa.altium.loader import SolveSettings
from fypa.tomachie_pdn import TomachiePdn, ambient_max_c, read_pdn_ambient
from fypa.viewer.tomachie_menu import take_board_ambient

from test_tomachie_pdn import PDN


def _notes(ambient):
    pdn = copy.deepcopy(PDN)
    pdn["ambient_max_c"] = ambient
    return TomachiePdn(pdn, "typ").parameters()[1]


def test_the_file_gives_the_board_temperature():
    assert ambient_max_c({"ambient_max_c": 85.0}) == 85.0
    assert any("maximum ambient is 85 °C" in n for n in _notes(85.0))


@pytest.mark.parametrize("value", ["unknown", None, True])
def test_no_stated_ambient_keeps_the_setting(value):
    assert ambient_max_c({"ambient_max_c": value}) is None
    assert any("states no maximum ambient" in n for n in _notes(value))


def test_read_from_the_file(tmp_path):
    f = tmp_path / "a.pdn.json"
    f.write_text(json.dumps({"ambient_max_c": 70}), encoding="utf-8")
    assert read_pdn_ambient(f) == 70


def test_loading_the_file_sets_the_board_temperature():
    window = SimpleNamespace(_solve_settings=SolveSettings())
    take_board_ambient(window, {"ambient_max_c": 85.0})
    assert window._solve_settings.temperature_c == 85.0
    take_board_ambient(window, {"ambient_max_c": "unknown"})
    assert window._solve_settings.temperature_c == 85.0


def test_copper_at_the_ambient_is_more_resistive():
    cold, warm = SolveSettings(), SolveSettings(temperature_c=85.0)
    ratio = cold.copper_conductivity_s_per_mm / warm.copper_conductivity_s_per_mm
    assert ratio == pytest.approx(1 + 0.00393 * 65.0)


VIA_PLATING = {"through_um": 20, "blind_um": 20, "buried_um": 13, "microvia_um": 12,
               "basis": "ipc6012_class2_min"}


def test_the_file_gives_the_via_plating():
    from fypa.tomachie_pdn import via_plating_mm
    mm = via_plating_mm({"via_plating": VIA_PLATING})
    assert mm == pytest.approx({"plating_thickness_mm": 0.020, "plating_blind_mm": 0.020,
                                "plating_buried_mm": 0.013, "plating_microvia_mm": 0.012})
    assert via_plating_mm({}) == {}
    pdn = copy.deepcopy(PDN)
    pdn["via_plating"] = VIA_PLATING
    notes = TomachiePdn(pdn, "typ").parameters()[1]
    assert any("Via plating from the file (ipc6012_class2_min): through 20 um" in n for n in notes)


def test_loading_the_file_sets_the_via_plating():
    from fypa.viewer.tomachie_menu import take_board_via_plating
    window = SimpleNamespace(_solve_settings=SolveSettings())
    take_board_via_plating(window, {"via_plating": VIA_PLATING})
    assert window._solve_settings.plating_thickness_mm == pytest.approx(0.020)
    assert window._solve_settings.plating_microvia_mm == pytest.approx(0.012)
    take_board_via_plating(window, {})
    assert window._solve_settings.plating_thickness_mm == pytest.approx(0.020)
