"""PDN directives from a Tomachie power-tree file (``<design>.pdn.json``).

Tomachie reads the schematic and the parts' datasheets and writes every rail, its
voltage and source, the regulators (type, efficiency, input and output rails), the
parts in series between rails with their DC resistance, and every load with its
typical and maximum current and its datasheet minimum supply voltage. FYPA otherwise
takes that setup from ``PDN_*`` parameters the designer types onto each component.

This module turns the file into exactly those parameters: one indexed channel
(``PDN1_*``, ``PDN2_*`` ...) per role a component plays on a rail, merged into the
schematic components before annotation parsing, so everything downstream treats them
like schematic directives. A component that already carries ``PDN_*`` parameters is
left alone: the designer's own annotation wins.

Mapping:

* a rail sourced by a connector, battery or module pin -> ``SOURCE`` on that part;
* a rail sourced by a regulator -> ``REGULATOR`` (``LDO`` / ``SMPS``, efficiency) with
  ``OUT_P_NET`` the net its output pins sit on (a switcher's switch node) and
  ``IN_P_NET`` the net its input pins sit on;
* a part in series (a fuse, ferrite, sense resistor, pass FET, a switcher's inductor)
  -> ``SERIES`` with its DC resistance; a 0 ohm link is left to FYPA's own auto-bridge;
* a load -> ``SINK`` with ``PDN_I`` (typical, or maximum with ``corner="max"``) and
  ``PDN_MIN_V`` from the datasheet minimum supply voltage.

Nothing is guessed. A load whose current the file does not know gets no SINK (FYPA
has no "not known" current), and every such omission is returned as a note, so the
user sees that the affected rail's drop is a lower bound.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCHEMA = "tomachie-pdn"
SCHEMA_VERSION = 1
SOURCE_ROLES = ("board_input", "battery", "generator")
REGULATOR_TYPE = {"linear": "LDO", "switching": "SMPS"}
DC_RESISTANCE_SPECS = ("dcr", "dc_resistance", "resistance", "rds_on", "jumper_resistance",
                       "cold_resistance", "r_dc", "rdc", "on_resistance")
OHMS = {"ohm": 1.0, "ohms": 1.0, "Ω": 1.0, "mohm": 1e-3, "mΩ": 1e-3,
        "kohm": 1e3, "kΩ": 1e3}


class PdnImportError(ValueError):
    pass


def _number(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _amps(current) -> Optional[float]:
    return _number(current.get("amps")) if isinstance(current, dict) else None


def _pads(refdes: str, pins: List[str]) -> List[str]:
    """``("U5", ["U5_10", "U5_A1"])`` -> ``["10", "A1"]``: the part's own reference and
    the underscore after it come off the front, so a pad name may hold underscores."""
    prefix = f"{refdes}_"
    return [p[len(prefix):] if p.startswith(prefix) else p.rpartition("_")[2] for p in pins]


def _printed_ohms(fig: dict, column: str) -> Optional[float]:
    unit = str(fig.get("unit", "")).strip()
    scale = OHMS.get(unit, OHMS.get(unit.lower()))
    try:
        value = float(str(fig.get(column, "")).strip().lstrip("<>~"))
    except ValueError:
        return None
    return None if scale is None else abs(value) * scale


def dc_resistance(element: dict) -> Tuple[Optional[float], Optional[float]]:
    """(typical, maximum) DC resistance of a series part, ohms. Only figures that ARE a
    DC resistance count: a ferrite's impedance at 100 MHz is in ohms too."""
    own = _number(element.get("resistance_ohm"))
    if own is not None:
        return own, None
    figs = [f for f in element.get("datasheet_figures", [])
            if str(f.get("spec", "")).lower() in DC_RESISTANCE_SPECS]
    typs = [v for v in (_printed_ohms(f, "typ") for f in figs) if v is not None]
    maxs = [v for v in (_printed_ohms(f, "max") for f in figs) if v is not None]
    return (max(typs) if typs else None), (max(maxs) if maxs else None)


class _Channels:
    """Indexed PDN parameters per designator: PDN1_ROLE, PDN1_V, ... PDN2_ROLE ..."""

    def __init__(self):
        self.params: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.count: Dict[str, int] = defaultdict(int)
        self.keys: Dict[str, set] = defaultdict(set)

    def add(self, designator: str, key: tuple, values: Dict[str, str]) -> bool:
        """One channel; a channel already added under the same key is not repeated."""
        if key in self.keys[designator]:
            return False
        self.keys[designator].add(key)
        self.count[designator] += 1
        n = self.count[designator]
        for k, v in values.items():
            self.params[designator][f"PDN{n}_{k}"] = v
        return True


class TomachiePdn:
    def __init__(self, pdn: dict, corner: str = "typ"):
        if pdn.get("schema") != SCHEMA or pdn.get("schema_version") != SCHEMA_VERSION:
            raise PdnImportError(f"Not a {SCHEMA} version {SCHEMA_VERSION} file")
        if corner not in ("typ", "max"):
            raise PdnImportError(f"corner must be 'typ' or 'max', not {corner!r}")
        self.pdn, self.corner = pdn, corner
        grounds = pdn.get("ground_nets") or []
        if not grounds:
            raise PdnImportError("The file names no ground net")
        self.gnd = grounds[0]
        self.grounds = list(grounds)
        # (regulator, net its input pins sit on, its input rail) - joined on the board
        self.reg_inputs: List[Tuple[str, str, str]] = []
        self.rails = {r["name"]: r for r in pdn.get("rails", [])}
        self.regs = {g["refdes"]: g for g in pdn.get("regulators", [])}
        self.ch = _Channels()
        self.notes: List[str] = []
        self.powered: set = set()          # rails something in the file drives
        self.pending_regs: List[tuple] = []  # (rail, refdes, values, input rail, output_path)

    # ------------------------------------------------------------ roles
    def _sources(self):
        for name, r in self.rails.items():
            src = r["source"]
            v = _number(r.get("voltage", {}).get("nominal"))
            if src["role"] == "unresolved":
                self.notes.append(f"Rail {name}: no source traced in the schematic")
            elif src["role"] in SOURCE_ROLES:
                if v is None or not src.get("pins"):
                    self.notes.append(f"Rail {name}: source {src.get('refdes')} has no stated "
                                      "voltage or pins; no SOURCE")
                    continue
                self.ch.add(src["refdes"], ("SOURCE", name), {
                    "ROLE": "SOURCE", "V": f"{v:g}", "P_NET": src.get("output_net") or name,
                    "N_NET": self.gnd, "P_PINS": ",".join(_pads(src["refdes"], src["pins"]))})
                self.powered.add(name)

    def _input(self, reg: dict) -> Tuple[Optional[str], List[str], Optional[str]]:
        """The net the regulator's input pins sit on, those pins, and the input rail."""
        for rail in reg.get("input_rails", []):
            for l in self.rails.get(rail, {}).get("loads", []):
                if l["role"] == "regulator" and l["refdes"] == reg["refdes"]:
                    return (l.get("input_net") or rail, _pads(l["refdes"], l.get("pins", [])),
                            rail)
        return None, [], None

    def _regulators(self):
        for name, r in self.rails.items():
            src = r["source"]
            if src["role"] != "regulator":
                continue
            reg = self.regs.get(src.get("refdes", ""))
            v = _number(r.get("voltage", {}).get("nominal"))
            kind = REGULATOR_TYPE.get((reg or {}).get("type"))
            vin, vin_pins, vin_rail = self._input(reg) if reg else (None, [], None)
            if reg is None or v is None or kind is None or vin is None:
                why = ("no regulator entry" if reg is None else "no stated voltage" if v is None
                       else "not known whether linear or switching" if kind is None
                       else "its input pins are not in the file")
                self.notes.append(f"{src.get('refdes')} ({name}): {why}; no REGULATOR, so "
                                  f"{name} is not driven")
                continue
            values = {"ROLE": "REGULATOR", "V": f"{v:g}", "REGULATOR_TYPE": kind,
                      "OUT_P_NET": src.get("output_net") or name, "OUT_N_NET": self.gnd,
                      "IN_P_NET": vin, "IN_N_NET": self.gnd,
                      "OUT_P_PINS": ",".join(_pads(reg["refdes"], src.get("pins", []))),
                      "IN_P_PINS": ",".join(vin_pins)}
            iq = _amps(reg.get("quiescent_current"))
            if iq is not None:
                values["QUIESCENT"] = f"{iq:g}"
            eff = _number(reg.get("efficiency"))
            if kind == "SMPS":
                if eff is None:
                    self.notes.append(f"{reg['refdes']} ({name}): efficiency not read from its "
                                      "datasheet; FYPA's default (1.0, lossless) is used, so "
                                      "its input current is understated")
                else:
                    values["REGULATOR_EFFICIENCY"] = f"{eff:g}"
            self.pending_regs.append((name, reg["refdes"], values, vin_rail,
                                      src.get("output_path", [])))

    def _power(self):
        """Which rails the file drives, to a fixed point: a source's rail; a regulator's
        output when its input rail is driven; a branch rail behind series parts when its
        parent is. Only those get directives - a sink on a rail nothing drives draws
        current from nowhere, and the solve has no answer (FYPA shows it as a huge
        ground-node current and kilovolt readings)."""
        changed = True
        while changed:
            changed = False
            for name, r in self.rails.items():
                if name not in self.powered and r.get("parent_rail") in self.powered:
                    self.powered.add(name)
                    changed = True
            for rail, *_rest in self.pending_regs:
                vin_rail = _rest[2]
                if rail not in self.powered and vin_rail in self.powered:
                    self.powered.add(rail)
                    changed = True
        for rail, ref, values, vin_rail, path in self.pending_regs:
            if rail not in self.powered:
                self.notes.append(f"{ref} ({rail}): nothing in the file drives its input "
                                  f"({vin_rail}); no REGULATOR")
                continue
            self.ch.add(ref, ("REGULATOR", rail), values)
            if values["IN_P_NET"] != vin_rail:
                self.reg_inputs.append((ref, values["IN_P_NET"], vin_rail))
            for e in path:
                if e.get("net_in") and e.get("net_out"):
                    self._series(e)

    def _series(self, e: dict):
        ref = e["refdes"]
        typ, mx = dc_resistance(e)
        r = mx if self.corner == "max" and mx is not None else (typ if typ is not None else mx)
        if e.get("class") == "diode":
            # FYPA has no forward-voltage element. Bridged so the solve reaches what is
            # behind it (Ivy Plus D2, the reverse-polarity diode ahead of U12); the rail
            # behind it then sits one forward drop higher than on the board.
            self.notes.append(f"{ref}: series diode - its forward voltage is not modelled "
                              "(bridged at 1 mOhm); the rail after it reads one forward "
                              "drop high")
            self.ch.add(ref, ("SERIES", e.get("net_in"), e.get("net_out")), {
                "ROLE": "SERIES", "R": "0.001", "P_NET": e["net_in"], "N_NET": e["net_out"]})
            return
        if r is None:
            # Dropping it would cut the rail off from its source (Ivy Plus F1, the input
            # fuse); bridged and named, so the drop across it is visibly not modelled.
            self.notes.append(f"{ref}: DC resistance not in the file; bridged at 1 mOhm")
            r = 0.001
        if r <= 0:
            return                          # a 0 ohm link: FYPA bridges it itself
        self.ch.add(ref, ("SERIES", e.get("net_in"), e.get("net_out")), {
            "ROLE": "SERIES", "R": f"{r:g}", "P_NET": e["net_in"], "N_NET": e["net_out"]})

    def _branches(self):
        for r in self.rails.values():
            if r["name"] not in self.powered:
                continue
            for e in r.get("series_from_parent", []):
                if e.get("net_in") and e.get("net_out"):
                    self._series(e)

    def _loads(self):
        for name, r in self.rails.items():
            if name not in self.powered:
                drawn = [l["refdes"] for l in r.get("loads", []) if l["role"] != "regulator"]
                if drawn:
                    self.notes.append(f"Rail {name}: nothing in the file drives it; its loads "
                                      f"are left out: {', '.join(drawn)}")
                continue
            for l in r.get("loads", []):
                if l["role"] == "regulator":
                    continue                # carried by the regulator's input
                self._sink(name, l)
            for pin in r.get("offboard_pins", []):
                self.notes.append(f"{name}: power leaves the board at {pin}; that load is not "
                                  "in the file, so the rail's drop is a lower bound")

    def _sink_current(self, l: dict) -> Optional[float]:
        if l.get("load_dependent"):
            return None
        pick = (_amps(l.get("current_max")) if self.corner == "max" else None) \
            or _amps(l.get("current"))
        if pick is not None and l["role"] == "ic_with_regulator":
            reg = (_amps(l.get("regulator_input_max")) if self.corner == "max" else None) \
                or _amps(l.get("regulator_input"))
            pick = None if reg is None else pick + reg
        return pick

    def _sink(self, rail: str, l: dict):
        amps = self._sink_current(l)
        if amps is None:
            why = l.get("load_dependent_reason") if l.get("load_dependent") else "current not known"
            self.notes.append(f"{l['refdes']} on {rail}: {why}; no SINK, so {rail}'s drop is a "
                              "lower bound")
            return
        net = l.get("input_net") or rail
        pins = ",".join(_pads(l["refdes"], l.get("pins", [])))
        values = {"ROLE": "SINK", "I": f"{amps:g}", "P_NET": net, "N_NET": self.gnd,
                  "P_PINS": pins}
        if l["role"] == "resistive_path":
            # A resistor / LED chain reaches ground through its LAST part, not the one
            # on the rail: the return terminal is that part's ground pads (PDN_N_DES).
            # Two-terminal like every other sink - FYPA does not mix modes on a rail.
            path = l.get("path_parts") or []
            if not path:
                self.notes.append(f"{l['refdes']} on {rail}: resistive path with no parts "
                                  "listed to ground; no SINK")
                return
            values["N_DES"] = path[-1]
        sm = l.get("supply_min")
        if isinstance(sm, dict) and _number(sm.get("volts")) is not None:
            values["MIN_V"] = f"{_number(sm['volts']):g}"
        self.ch.add(l["refdes"], ("SINK", net), values)

    # ------------------------------------------------------------ the board
    def series_elements(self) -> Dict[str, dict]:
        """Every part the file lists in series, by designator (for its DC resistance)."""
        out: Dict[str, dict] = {}
        for r in self.rails.values():
            for e in r.get("series_from_parent", []) + (r.get("source") or {}).get("output_path", []):
                out.setdefault(e.get("refdes", ""), e)
        return out

    def place_on_board(self, params: Dict[str, Dict[str, str]], extracted) -> List[str]:
        """What only the board can answer, applied to ``params`` in place:

        * a source's return is the ground net its pads are on (Ivy Plus J3: V-, not GND);
          when that is not the main ground, the common-mode choke the file lists in series
          carries the return through its second winding, at the same DC resistance;
        * a regulator whose input pins sit behind an input filter (U12: VIN -> C69 -> L10
          -> its VIN pin) gets the filter's parts as SERIES, so the solve reaches its input.
        """
        notes: List[str] = []
        pads = defaultdict(set)        # designator -> nets its pads are on
        for pad in extracted.pads:
            if pad.component_index < 0:
                continue
            comp = extracted.pcb_components[pad.component_index]
            des = (comp.source_designator or comp.designator).upper()
            pads[des].add(extracted.net_name(pad.net_index))
        grounds = set(self.grounds)
        known = {k.upper(): v for k, v in self.series_elements().items()}

        def ohms(des: str) -> Tuple[float, str]:
            e = known.get(des)
            if e is not None:
                typ, mx = dc_resistance(e)
                r = mx if self.corner == "max" and mx is not None else (typ if typ is not None else mx)
                if r is not None and r > 0:
                    return r, ""
            return 0.001, f"{des}: DC resistance not in the file; bridged at 1 mOhm (above the 0.9 mOhm net-merge threshold)"

        def add(des: str, values: Dict[str, str]):
            ch = params.setdefault(des, {})
            used = [int(k[3:].split("_", 1)[0]) for k in ch
                    if k.startswith("PDN") and k[3:].split("_", 1)[0].isdigit()]
            n = 1 + max(used or [0])
            for k, v in values.items():
                ch[f"PDN{n}_{k}"] = v

        # 1) source returns
        for des, ch in list(params.items()):
            on = pads.get(des.upper(), set())
            for k, v in list(ch.items()):
                if not (k.endswith("_ROLE") and v == "SOURCE"):
                    continue
                nk = k[:-len("ROLE")] + "N_NET"
                if ch.get(nk) in on:
                    continue
                ret = sorted(on & grounds)
                if not ret:
                    notes.append(f"{des}: no pad on any ground net of the file; return left as {ch.get(nk)}")
                    continue
                ch[nk] = ret[0]
                notes.append(f"{des}: source return is {ret[0]} (its pads), not {self.gnd}")
                if ret[0] == self.gnd:
                    continue
                choke = [c for c, nets in pads.items()
                         if c in known and ret[0] in nets and self.gnd in nets]
                if not choke:
                    notes.append(f"{des}: nothing the file lists joins {ret[0]} to {self.gnd}; "
                                 "the return is not closed")
                    continue
                r, why = ohms(choke[0])
                if why:
                    notes.append(why)
                add(choke[0], {"ROLE": "SERIES", "R": f"{r:g}", "P_NET": ret[0], "N_NET": self.gnd})
                notes.append(f"{choke[0]}: return winding {ret[0]} -> {self.gnd}, {r:g} Ohm")

        # 2) regulator input filters: the passive parts between the input pins' net and the rail
        def passive(des: str) -> bool:
            return des[:1] in ("L", "R", "C", "F") or des.startswith("FB")

        edges = defaultdict(list)       # net -> [(designator, other net)]
        for des, nets in pads.items():
            if not passive(des):
                continue
            sig = sorted(n for n in nets if n not in grounds)
            if len(sig) == 2:
                edges[sig[0]].append((des, sig[1]))
                edges[sig[1]].append((des, sig[0]))
        placed = set()
        for ref, pin_net, rail in self.reg_inputs:
            prev = {pin_net: None}
            frontier = [pin_net]
            for _ in range(4):
                nxt = []
                for n in frontier:
                    for des, other in edges.get(n, []):
                        if other not in prev:
                            prev[other] = (des, n)
                            nxt.append(other)
                frontier = nxt
                if rail in prev:
                    break
            if rail not in prev:
                notes.append(f"{ref}: no passive path on the board from its input pins ({pin_net}) "
                             f"to {rail}")
                continue
            n = rail
            while prev[n] is not None:
                des, back = prev[n]
                if (des, n, back) in placed:     # a filter two regulators share (U29, U30: L26)
                    n = back
                    continue
                placed.add((des, n, back))
                r, why = ohms(des)
                if why:
                    notes.append(why)
                add(des, {"ROLE": "SERIES", "R": f"{r:g}", "P_NET": n, "N_NET": back})
                notes.append(f"{ref}: input filter {des} {n} -> {back}")
                n = back
        return notes

    # ------------------------------------------------------------ run
    def parameters(self) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
        self._sources()
        self._regulators()
        self._power()
        self._branches()
        self._loads()
        return dict(self.ch.params), self.notes


def read_pdn_parameters(path, corner: str = "typ") -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    """``{designator: {PDNn_*: value}}`` and notes, from a Tomachie ``.pdn.json``."""
    pdn = json.loads(Path(path).read_text(encoding="utf-8"))
    return TomachiePdn(pdn, corner).parameters()


def read_pdn_for_board(path, corner: str, extracted) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    """As :func:`read_pdn_parameters`, then completed from the board itself
    (:meth:`TomachiePdn.place_on_board`)."""
    pdn = json.loads(Path(path).read_text(encoding="utf-8"))
    reader = TomachiePdn(pdn, corner)
    params, notes = reader.parameters()
    return params, notes + reader.place_on_board(params, extracted)


def _has_pdn(params: Dict[str, str]) -> bool:
    return any(k.upper().startswith("PDN") for k in params)


def apply_to_project(extracted, params: Dict[str, Dict[str, str]]):
    """A copy of ``extracted`` whose schematic components carry the file's parameters.
    Returns (project, notes). A component with its own PDN_* parameters keeps them."""
    notes: List[str] = []
    wanted = {d.upper(): (d, p) for d, p in params.items()}
    placed = set()
    comps = []
    for comp in extracted.sch_components:
        hit = wanted.get(comp.designator.upper())
        if hit is None:
            comps.append(comp)
            continue
        placed.add(comp.designator.upper())
        if _has_pdn(comp.parameters):
            notes.append(f"{comp.designator}: keeps its own schematic PDN_* parameters; the "
                         "file's are not applied")
            comps.append(comp)
            continue
        comps.append(replace(comp, parameters={**comp.parameters, **hit[1]}))
    for des_u, (des, _) in wanted.items():
        if des_u not in placed:
            notes.append(f"{des}: in the file but not in the schematic")
    return replace(extracted, sch_components=tuple(comps)), notes


def applied_designators(params: Dict[str, Dict[str, str]], extracted) -> set:
    """Upper-cased designators whose directives came from the file (not those that
    kept their own schematic annotation)."""
    own = {c.designator.upper() for c in extracted.sch_components if _has_pdn(c.parameters)}
    return {d.upper() for d in params} - own
