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
              "resistance_ohm": 0.0, "datasheet_figures": []},
             # Not in any regulation loop: its DCR is a real drop, typ or max by corner.
             {"refdes": "FB2", "class": "ferrite", "net_in": "+3V3", "net_out": "+3V3A",
              "resistance_ohm": "unknown",
              "datasheet_figures": [{"spec": "dcr", "typ": "45", "max": "60", "unit": "mΩ"},
                                    {"spec": "impedance", "typ": "600", "unit": "ohm"}]}],
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
    p, notes = params()
    u2 = p["U2"]
    assert (u2["PDN1_ROLE"], u2["PDN1_REGULATOR_TYPE"], u2["PDN1_REGULATOR_EFFICIENCY"]) == \
        ("REGULATOR", "SMPS", "0.88")
    assert (u2["PDN1_OUT_P_NET"], u2["PDN1_IN_P_NET"], u2["PDN1_IN_P_PINS"]) == ("SW", "VBUS", "2,3")
    l1 = p["L1"]
    assert (l1["PDN1_ROLE"], l1["PDN1_P_NET"], l1["PDN1_N_NET"]) == ("SERIES", "SW", "+3V3")
    # The output inductor is inside U2's regulation loop (feedback after it), so
    # it is bridged at 1 mOhm - above the 0.9 mOhm net-merge threshold - and its
    # DCR (64 mOhm) is not a rail drop; its loss is in the efficiency.
    assert float(l1["PDN1_R"]) == pytest.approx(0.001)
    assert any("L1: inside U2's regulation loop; bridged" in n for n in notes)


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
    # A switcher's output inductor stays bridged in the worst case too: its
    # maximum DCR (68 mOhm) is inside the regulation loop as well.
    assert float(p["L1"]["PDN1_R"]) == pytest.approx(0.001)


def test_series_dcr_typical_nominally_maximum_in_the_worst_case():
    nominal, _ = params()
    worst, _ = params("max")
    # DCR, never the impedance; typical in the nominal corner, maximum in the worst.
    assert float(nominal["FB2"]["PDN1_R"]) == pytest.approx(0.045)
    assert float(worst["FB2"]["PDN1_R"]) == pytest.approx(0.060)
    assert (nominal["FB2"]["PDN1_P_NET"], nominal["FB2"]["PDN1_N_NET"]) == ("+3V3", "+3V3A")


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


def test_project_named_in_the_file_is_found_up_to_two_folders_above(tmp_path):
    from fypa.viewer.tomachie_menu import _project_for
    prj = tmp_path / "board.PrjPcb"
    prj.write_text("")
    pdn = tmp_path / "output" / "enrich" / "board.pdn.json"
    pdn.parent.mkdir(parents=True)
    assert _project_for(None, pdn, {"project_file": "board.PrjPcb"}) == prj


def test_nothing_is_set_on_a_rail_nothing_drives():
    pdn = copy.deepcopy(PDN)
    pdn["rails"][0]["source"]["pins"] = []          # VBUS has no source now
    p, notes = params(pdn=pdn)
    # VBUS undriven -> U2's +3V3 and U3's +1V8 undriven -> +3V3A (behind FB1) too.
    for ref in ("U2", "U3", "U5", "U7", "R17", "L1", "FB1"):
        assert ref not in p, ref
    assert any("Rail +3V3: nothing in the file drives it" in n for n in notes)
    assert any("U2 (+3V3): nothing in the file drives its input (VBUS)" in n for n in notes)


def test_a_file_written_for_another_project_is_noticed():
    from pathlib import Path
    from fypa.viewer.tomachie_menu import written_for_another_project
    pdn = {"project_file": "board.PrjPcb"}
    assert written_for_another_project(pdn, Path("x/BOARD.PrjPcb")) is None
    assert written_for_another_project(pdn, Path("x/other.PrjPcb")) == "board.PrjPcb"
    assert written_for_another_project({}, Path("x/other.PrjPcb")) is None


SWITCH_NODE = {"net": "SW", "pins": ["U2_8"], "rectifier": "diode",
               "freewheel_diode": {"refdes": "D9", "part_number": "SS34",
                                   "anode_pins": ["D9_2"], "cathode_pins": ["D9_1"],
                                   "anode_net": "GND"}}


def _with_switch_node(switch_node):
    pdn = copy.deepcopy(PDN)
    pdn["spec_revision"] = 6
    pdn["regulators"][0]["switch_node"] = switch_node
    return pdn


def test_non_synchronous_buck_puts_its_freewheel_diode_on_the_switch_node():
    p, notes = params(pdn=_with_switch_node(SWITCH_NODE))
    # D = output rail nominal / input rail nominal = 3.3 V / 5 V.
    assert p["D9"] == {"PDN1_ROLE": "FREEWHEEL", "PDN1_DUTY": "0.66", "PDN1_SMPS_HOST": "U2",
                       "PDN1_P_NET": "GND", "PDN1_N_NET": "SW", "PDN1_P_PINS": "2",
                       "PDN1_N_PINS": "1"}
    assert p["U2"]["PDN1_OUT_P_PINS"] == "8"
    assert any("D9: freewheel diode of U2" in n for n in notes)


def test_synchronous_or_absent_switch_node_changes_nothing():
    before, _ = params()
    sync, _ = params(pdn=_with_switch_node(dict(SWITCH_NODE, rectifier="synchronous")))
    assert sync == before and "D9" not in sync


def test_diode_switch_node_without_its_pads_is_said_and_not_guessed():
    partial = copy.deepcopy(SWITCH_NODE)
    del partial["freewheel_diode"]["anode_pins"]
    before, _ = params()
    p, notes = params(pdn=_with_switch_node(partial))
    assert p == before
    assert any("U2 (+3V3): non-synchronous" in n and "anode_pins" in n for n in notes)


def test_rectifier_to_the_output_or_unknown_changes_nothing():
    before, _ = params()
    for rect in ("diode_to_output", "something_new"):
        sn = {"net": "SW", "pins": ["U2_8"], "rectifier": rect}
        p, _ = params(pdn=_with_switch_node(sn))
        assert p == before, rect


def test_source_return_from_the_file():
    pdn = copy.deepcopy(PDN)
    pdn["ground_nets"] = ["GND", "V-"]
    pdn["rails"][0]["source"]["return"] = {
        "pins": ["J1_A1"], "net": "V-", "ground_net": "GND",
        "path": [{"refdes": "L9", "class": "inductor", "net_in": "GND", "net_out": "V-",
                  "resistance_ohm": "unknown",
                  "datasheet_figures": [{"spec": "dcr", "max": "0.002", "unit": "ohm"}]}]}
    p, _ = params(pdn=pdn)
    j1 = p["J1"]
    assert (j1["PDN1_N_NET"], j1["PDN1_N_PINS"]) == ("V-", "A1")
    assert p["L9"] == {"PDN1_ROLE": "SERIES", "PDN1_R": "0.002", "PDN1_P_NET": "GND",
                       "PDN1_N_NET": "V-"}


def _with_load_switch(**reg):
    """VBUS -> U4 (a USB power switch, 5 V in, 5 V out) -> U4_OUT -> FB4 -> VBUS_OUT -> U9."""
    pdn = copy.deepcopy(PDN)
    pdn["spec_revision"] = 6
    pdn["rails"][0]["loads"].append({"role": "regulator", "refdes": "U4", "pins": ["U4_2", "U4_3"]})
    pdn["rails"].append(
        {"name": "VBUS_OUT", "voltage": {"nominal": 5.0, **V},
         "source": {"role": "regulator", "refdes": "U4", "pins": ["U4_6", "U4_7"],
                    "output_net": "U4_OUT", "output_path": [
                        {"refdes": "FB4", "class": "ferrite", "net_in": "U4_OUT",
                         "net_out": "VBUS_OUT", "resistance_ohm": "unknown",
                         "datasheet_figures": [{"spec": "dcr", "typ": "50", "max": "80",
                                                "unit": "mΩ"}]}]},
         "loads": [{"role": "ic", "refdes": "U9", "pins": ["U9_1"], "current": {"amps": 0.5},
                    "current_max": {"amps": 0.9}}]})
    pdn["regulators"].append({"refdes": "U4", "type": "load_switch", "type_basis": "schematic",
                              "input_rails": ["VBUS"], "output_rails": ["VBUS_OUT"], **reg})
    return pdn


def test_load_switch_is_a_series_part_with_its_on_resistance():
    figs = [{"spec": "rds_on", "typ": "89", "max": "120", "unit": "mΩ", "page": 4}]
    pdn = _with_load_switch(datasheet_resistances=figs,
                            quiescent_current={"amps": 9e-5, "basis": "typ"})
    nominal, notes = params(pdn=pdn)
    worst, _ = params("max", pdn=pdn)
    u4 = nominal["U4"]
    # A SERIES from its input pins to its output pins, never a REGULATOR (an LDO
    # making 5 V from 5 V is out of regulation).
    assert {k: u4[k] for k in u4 if k.startswith("PDN1_")} == {
        "PDN1_ROLE": "SERIES", "PDN1_R": "0.089", "PDN1_P_NET": "VBUS",
        "PDN1_N_NET": "U4_OUT", "PDN1_P_PINS": "2,3", "PDN1_N_PINS": "6,7"}
    assert float(worst["U4"]["PDN1_R"]) == pytest.approx(0.120)
    # Its quiescent current is drawn at its input, as an LDO's is.
    assert (u4["PDN2_ROLE"], u4["PDN2_I"], u4["PDN2_P_NET"], u4["PDN2_P_PINS"]) == \
        ("SINK", "9e-05", "VBUS", "2,3")
    assert not any(v == "REGULATOR" for v in u4.values())
    # The ferrite after it is a real drop (no regulation loop): its DCR, by corner.
    assert (nominal["FB4"]["PDN1_P_NET"], nominal["FB4"]["PDN1_N_NET"]) == ("U4_OUT", "VBUS_OUT")
    assert (float(nominal["FB4"]["PDN1_R"]), float(worst["FB4"]["PDN1_R"])) ==         (pytest.approx(0.050), pytest.approx(0.080))
    assert nominal["U9"]["PDN1_P_NET"] == "VBUS_OUT"     # the rail behind it is driven
    assert any("U4: load switch, a SERIES" in n for n in notes)


def test_load_switch_without_an_on_resistance_is_bridged_and_said():
    p, notes = params(pdn=_with_load_switch())
    assert p["U4"]["PDN1_ROLE"] == "SERIES" and float(p["U4"]["PDN1_R"]) == pytest.approx(0.001)
    assert "PDN2_ROLE" not in p["U4"]                    # no quiescent current in the file
    assert any("U4: load switch, on-resistance not in the file; bridged at 1 mOhm" in n
               for n in notes)


def _board(parts):
    """A stand-in for the extracted project: ``{designator: [net of each pad]}``."""
    from types import SimpleNamespace as NS
    nets = sorted({n for pads in parts.values() for n in pads})
    comps = [NS(designator=d, source_designator=d) for d in parts]
    pads = [NS(component_index=i, net_index=nets.index(n))
            for i, d in enumerate(parts) for n in parts[d]]
    return NS(pads=pads, pcb_components=comps, net_name=lambda i: nets[i])


def test_input_filter_parts_take_their_dc_resistance_from_the_file():
    """VBUS -> C9 (feedthrough, no figure) -> VBUS_F1 -> FB5 || FB6 -> VBUS_F2 -> U2."""
    ferrite = {"class": "ferrite", "resistance_ohm": "unknown",
               "datasheet_figures": [{"spec": "dcr", "typ": "40", "max": "50", "unit": "mΩ"},
                                     {"spec": "impedance", "typ": "600", "unit": "ohm"}]}
    pdn = copy.deepcopy(PDN)
    pdn["spec_revision"] = 6
    pdn["rails"][0]["loads"][0].update(input_net="VBUS_F2", input_filter=[
        {"refdes": "C9", "class": "capacitor", "net_in": "VBUS", "net_out": "VBUS_F1",
         "resistance_ohm": "unknown", "datasheet_figures": []},
        dict(ferrite, refdes="FB5", net_in="VBUS_F1", net_out="VBUS_F2"),
        dict(ferrite, refdes="FB6", net_in="VBUS_F1", net_out="VBUS_F2")])
    board = _board({"C9": ["VBUS", "VBUS_F1", "GND"], "FB5": ["VBUS_F1", "VBUS_F2"],
                    "FB6": ["VBUS_F1", "VBUS_F2"], "U2": ["VBUS_F2", "SW", "GND"],
                    "J1": ["VBUS", "GND"]})
    for corner, r in (("typ", 0.040), ("max", 0.050)):
        reader = TomachiePdn(copy.deepcopy(pdn), corner)
        p, _ = reader.parameters()
        notes = reader.place_on_board(p, board)
        # Each ferrite its DCR, never its impedance; the parallel one too.
        for fb in ("FB5", "FB6"):
            assert (p[fb]["PDN1_P_NET"], p[fb]["PDN1_N_NET"]) == ("VBUS_F1", "VBUS_F2")
            assert float(p[fb]["PDN1_R"]) == pytest.approx(r), (corner, fb)
        # The feedthrough capacitor has no resistance figure: bridged and named.
        assert float(p["C9"]["PDN1_R"]) == pytest.approx(0.001)
        assert any(n.startswith("C9: DC resistance not in the file; bridged") for n in notes)
        assert not any(n.startswith("FB5: DC resistance not in the file") for n in notes)


def test_return_named_on_another_ground_with_no_path_is_closed_from_the_board():
    """J3: the file names its return V- with an empty path; every regulator
    returns to GND, so V- is joined to GND through the choke's second winding."""
    pdn = copy.deepcopy(PDN)
    pdn["ground_nets"] = ["GND", "V-"]
    pdn["rails"][0]["source"]["return"] = {"pins": ["J1_A1"], "net": "V-", "path": [],
                                           "ground_net": "V-"}
    pdn["rails"][2]["series_from_parent"].append(
        {"refdes": "L9", "class": "inductor", "net_in": "+3V3", "net_out": "+3V3A",
         "resistance_ohm": "unknown",
         "datasheet_figures": [{"spec": "dcr", "max": "0.002", "unit": "ohm"}]})
    reader = TomachiePdn(pdn, "typ")
    p, _ = reader.parameters()
    notes = reader.place_on_board(p, _board({"J1": ["VBUS", "V-"], "L9": ["V-", "GND"]}))
    assert p["J1"]["PDN1_N_NET"] == "V-"
    winding = {k[5:]: v for k, v in p["L9"].items() if k.startswith("PDN2_")}
    assert winding == {"ROLE": "SERIES", "R": "0.002", "P_NET": "V-", "N_NET": "GND"}
    assert any("L9: return winding V- -> GND" in n for n in notes)


def test_rails_sourced_from_the_same_output_pins_are_one_regulator():
    """U9_6 sources DVDD12 and, behind L7, AVDD12: one REGULATOR, not two in
    parallel (the report counted the shared load once per channel)."""
    pdn = copy.deepcopy(PDN)
    pdn["rails"].append(
        {"name": "+1V8A", "voltage": {"nominal": 1.8, **V},
         "source": {"role": "regulator", "refdes": "U3", "pins": ["U3_5"], "output_net": "+1V8",
                    "output_path": [{"refdes": "L7", "class": "ferrite", "net_in": "+1V8",
                                     "net_out": "+1V8A", "resistance_ohm": "unknown",
                                     "datasheet_figures": [{"spec": "dcr", "max": "0.1",
                                                            "unit": "ohm"}]}]},
         "loads": []})
    pdn["regulators"][1]["output_rails"] = ["+1V8", "+1V8A"]
    p, notes = params(pdn=pdn)
    assert [k for k in p["U3"] if k.endswith("_ROLE")] == ["PDN1_ROLE"]
    assert (p["L7"]["PDN1_P_NET"], p["L7"]["PDN1_N_NET"], p["L7"]["PDN1_R"]) == \
        ("+1V8", "+1V8A", "0.1")
    assert any("U3 (+1V8A): the same output pins as its REGULATOR for +1V8" in n for n in notes)


def test_worst_case_drives_a_source_at_its_minimum_voltage():
    """Spec: a worst-case solve drives each source at voltage.min (V+: 48 V
    nominal, 21 V min); a regulator output stays at its set voltage, and a buck's
    duty cycle follows the lower input."""
    pdn = _with_switch_node(SWITCH_NODE)
    pdn["rails"][0]["voltage"] = {"nominal": 5.0, "min": 4.0, "max": 5.5, "basis": "user"}
    nominal, _ = params(pdn=pdn)
    worst, notes = params("max", pdn=pdn)
    assert (nominal["J1"]["PDN1_V"], worst["J1"]["PDN1_V"]) == ("5", "4")
    assert worst["U2"]["PDN1_V"] == "3.3" and worst["U3"]["PDN1_V"] == "1.8"
    assert (float(nominal["D9"]["PDN1_DUTY"]), float(worst["D9"]["PDN1_DUTY"])) == \
        (pytest.approx(3.3 / 5.0), pytest.approx(3.3 / 4.0))
    assert any("Rail VBUS: worst case, source J1 at its minimum 4 V" in n for n in notes)
    # A minimum that is not a number leaves the source at its nominal.
    plain, _ = params("max")
    assert plain["J1"]["PDN1_V"] == "5"
