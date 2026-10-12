"""PDN_* parameters from a Tomachie power-tree file (fypa.tomachie_pdn)."""

import copy
from dataclasses import dataclass

import pytest

from fypa.tomachie_pdn import PdnImportError, TomachiePdn, apply_to_project, dc_resistance

V = {"min": "not_specified", "max": "not_specified", "basis": "net_name"}

PDN = {
    "schema": "tomachie-pdn", "schema_version": 1, "spec_revision": 3,
    "ground_nets": ["GND"],
    "rails": [
        {"name": "VBUS", "voltage": {"nominal": 5.0, **V},
         "source": {"role": "board_input", "refdes": "J1", "pins": ["J1_A4", "J1_B9"],
                    "output_net": "VBUS", "output_path": []},
         "loads": [{"role": "regulator", "refdes": "U2", "pins": ["U2_2", "U2_3"]},
                   {"role": "regulator", "refdes": "U3", "pins": ["U3_1"]}],
         "offboard_pins": ["J2_1"]},
        {"name": "+3V3", "voltage": {"nominal": 3.3, **V},
         "source": {"role": "regulator", "refdes": "U2", "pins": ["U2_8"], "output_net": "SW",
                    "output_path": [
                        {"refdes": "L1", "class": "inductor", "net_in": "SW", "net_out": "+3V3",
                         "resistance_ohm": "unknown", "datasheet_figures": [
                             {"spec": "dcr", "typ": "64", "max": "68", "unit": "mΩ"},
                             {"spec": "impedance", "typ": "120", "unit": "ohm"}]}]},
         "loads": [{"role": "ic", "refdes": "U5", "pins": ["U5_7", "U5_8"],
                    "current": {"amps": 0.2}, "current_max": {"amps": 0.35},
                    "supply_min": {"volts": 3.0, "basis": "min", "spec": "vdd", "page": 4}},
                   {"role": "ic", "refdes": "U6", "pins": ["U6_1"], "current": "unknown",
                    "current_max": "unknown", "supply_min": "unknown"},
                   {"role": "resistive_path", "refdes": "R17", "pins": ["R17_2"],
                    "path_parts": ["R17", "D3"], "current": {"amps": 0.008,
                                                             "basis": "v_over_r"}},
                   {"role": "ic", "refdes": "U8", "pins": ["U8_VDD_IO"],
                    "current": {"amps": 0.001}}]},
        {"name": "+3V3A", "voltage": {"nominal": 3.3, **V},
         "source": {"role": "parent_rail"}, "parent_rail": "+3V3",
         "series_from_parent": [
             {"refdes": "FB1", "class": "ferrite", "net_in": "+3V3", "net_out": "+3V3A",
              "resistance_ohm": "unknown",
              "datasheet_figures": [{"spec": "dcr", "max": "0.08", "unit": "ohm"}]},
             {"refdes": "R9", "class": "resistor", "net_in": "+3V3", "net_out": "+3V3A",
              "resistance_ohm": 0.0, "datasheet_figures": []}],
         "loads": [{"role": "ic", "refdes": "U7", "pins": ["U7_3"],
                    "current": {"amps": 0.01}, "current_max": {"amps": 0.02}}]},
        {"name": "+1V8", "voltage": {"nominal": 1.8, **V},
         "source": {"role": "regulator", "refdes": "U3", "pins": ["U3_5"], "output_net": "+1V8",
                    "output_path": []},
         "loads": []},
    ],
    "regulators": [
        {"refdes": "U2", "type": "switching", "efficiency": 0.88,
         "input_rails": ["VBUS"], "output_rails": ["+3V3"]},
        {"refdes": "U3", "type": "linear", "input_rails": ["VBUS"], "output_rails": ["+1V8"]},
    ],
}


def params(corner="typ", pdn=PDN):
    return TomachiePdn(copy.deepcopy(pdn), corner).parameters()


def test_source_on_the_connector_pads():
    p, _ = params()
    assert p["J1"] == {"PDN1_ROLE": "SOURCE", "PDN1_V": "5", "PDN1_P_NET": "VBUS",
                       "PDN1_N_NET": "GND", "PDN1_P_PINS": "A4,B9"}


def test_switcher_regulates_on_its_switch_node_with_the_inductor_in_series():
    p, _ = params()
    u2 = p["U2"]
    assert (u2["PDN1_ROLE"], u2["PDN1_REGULATOR_TYPE"], u2["PDN1_REGULATOR_EFFICIENCY"]) == \
        ("REGULATOR", "SMPS", "0.88")
    assert (u2["PDN1_OUT_P_NET"], u2["PDN1_IN_P_NET"], u2["PDN1_IN_P_PINS"]) == ("SW", "VBUS", "2,3")
    l1 = p["L1"]
    assert (l1["PDN1_ROLE"], l1["PDN1_P_NET"], l1["PDN1_N_NET"]) == ("SERIES", "SW", "+3V3")
    assert float(l1["PDN1_R"]) == pytest.approx(0.064)        # DCR, never the impedance


def test_ldo():
    p, _ = params()
    assert p["U3"]["PDN1_REGULATOR_TYPE"] == "LDO" and "PDN1_REGULATOR_EFFICIENCY" not in p["U3"]


def test_sink_with_datasheet_minimum():
    p, _ = params()
    assert p["U5"] == {"PDN1_ROLE": "SINK", "PDN1_I": "0.2", "PDN1_P_NET": "+3V3",
                       "PDN1_N_NET": "GND", "PDN1_P_PINS": "7,8", "PDN1_MIN_V": "3"}


def test_worst_case_corner_takes_the_maximums():
    p, _ = params("max")
    assert p["U5"]["PDN1_I"] == "0.35"
    assert float(p["L1"]["PDN1_R"]) == pytest.approx(0.068)


def test_ferrite_branch_and_zero_ohm_link():
    p, _ = params()
    assert p["FB1"]["PDN1_R"] == "0.08"           # only a maximum printed
    assert "R9" not in p                           # 0 ohm: FYPA's own auto-bridge


def test_what_cannot_be_set_is_said():
    p, notes = params()
    assert "U6" not in p
    assert any("U6 on +3V3: current not known" in n for n in notes)
    assert any("J2_1" in n and "lower bound" in n for n in notes)


def test_switcher_without_efficiency_is_said():
    pdn = copy.deepcopy(PDN)
    pdn["regulators"][0]["efficiency"] = "unknown"
    p, notes = params(pdn=pdn)
    assert "PDN1_REGULATOR_EFFICIENCY" not in p["U2"]
    assert any("U2 (+3V3): efficiency not read" in n for n in notes)


def test_wrong_schema_and_corner_are_refused():
    with pytest.raises(PdnImportError):
        TomachiePdn({"schema": "x"})
    with pytest.raises(PdnImportError):
        TomachiePdn(copy.deepcopy(PDN), "worst")


def test_dc_resistance_never_takes_an_impedance():
    assert dc_resistance({"resistance_ohm": "unknown", "datasheet_figures": [
        {"spec": "impedance_at_frequency", "typ": "330", "unit": "ohm"}]}) == (None, None)


@dataclass(frozen=True)
class _FakeProject:
    """apply_to_project reads and replaces only sch_components."""
    sch_components: tuple


def test_designer_annotation_wins_and_missing_parts_are_noted():
    from fypa.altium.extract import RawSchComponent
    comps = (RawSchComponent("U5", "a.SchDoc", {"PDN_ROLE": "SINK", "PDN_I": "1"}, ("7",)),
             RawSchComponent("J1", "a.SchDoc", {"Value": "USB-C"}, ("A4",)))
    p, _ = params()
    out, notes = apply_to_project(_FakeProject(comps), p)
    by = {c.designator: c.parameters for c in out.sch_components}
    assert by["U5"] == {"PDN_ROLE": "SINK", "PDN_I": "1"}
    assert by["J1"]["PDN1_ROLE"] == "SOURCE" and by["J1"]["Value"] == "USB-C"
    assert any("U5: keeps its own" in n for n in notes)
    assert any("U2: in the file but not in the schematic" in n for n in notes)


def test_resistive_path_returns_through_its_last_part():
    p, _ = params()
    assert p["R17"] == {"PDN1_ROLE": "SINK", "PDN1_I": "0.008", "PDN1_P_NET": "+3V3",
                        "PDN1_N_NET": "GND", "PDN1_P_PINS": "2", "PDN1_N_DES": "D3"}


def test_pad_name_with_an_underscore_survives():
    p, _ = params()
    assert p["U8"]["PDN1_P_PINS"] == "VDD_IO"


def test_regulator_quiescent_current():
    pdn = copy.deepcopy(PDN)
    pdn["regulators"][1]["quiescent_current"] = {"amps": 5.5e-5, "basis": "typ"}
    p, _ = params(pdn=pdn)
    assert p["U3"]["PDN1_QUIESCENT"] == "5.5e-05"
