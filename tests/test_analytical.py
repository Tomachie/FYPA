"""Analytical benchmark tests for the FEM solver.

These pin solver output against closed-form physics. A uniform copper strip
carrying a known current has a potential field that, away from the injection
points, is linear in the length coordinate with slope

    dV/dx  =  -I / (G * W)

where ``G`` is the sheet conductance (S) and ``W`` the strip width (mm).
Measuring the slope over the strip *interior* isolates the bulk sheet
resistance from the 2-D point-source singularity at the injection terminals.

Note on accuracy: even refined, the solver retains a documented ~5 %
under-estimation of sheet resistance for point-injected current (see
``pdnsolver/CHANGES.md`` — the Steiner-ring seeding brings a 1 mm x 100 mm
trace "within ~5 % of theory"). The absolute test below therefore uses an
8 % gate; the *relational* tests hold the geometry fixed so that bias cancels
exactly and can be checked tightly.
"""
from __future__ import annotations

import pytest

# Absolute agreement is limited by the documented ~5 % point-injection bias
# plus mesh discretisation; 8 % is a regression gate that still catches the
# ~45 % error seen when ring-seeding is absent.
SLOPE_ABS_TOL = 0.08
# Relational checks hold geometry fixed, so the bias is a common factor that
# cancels — these are exact up to solver precision.
SLOPE_REL_TOL = 0.02


@pytest.mark.parametrize(
    ("length_mm", "width_mm"),
    [
        (40.0, 6.0),    # baseline
        (80.0, 6.0),    # longer trace
        (40.0, 3.0),    # narrower trace
        (40.0, 10.0),   # wider pour
    ],
)
def test_strip_interior_slope_matches_sheet_resistance(strip_solver, length_mm, width_mm):
    result = strip_solver(length_mm=length_mm, width_mm=width_mm, mesh_size=0.3)

    measured = result.interior_slope()
    analytical = result.expected["slope_v_per_mm"]
    rel_err = abs(measured - analytical) / analytical

    assert rel_err < SLOPE_ABS_TOL, (
        f"strip {length_mm}x{width_mm} mm: interior dV/dx {measured:.4e} V/mm "
        f"deviates {rel_err:.1%} from analytical {analytical:.4e} V/mm"
    )


def test_solver_underestimates_sheet_resistance(strip_solver):
    """Characterisation: the point-injection FEM under-estimates IR drop.

    A flip of this sign means ring-seeding behaviour changed materially and
    ``pdnsolver/CHANGES.md`` should be revisited.
    """
    result = strip_solver(length_mm=40.0, width_mm=6.0, mesh_size=0.3)
    measured = result.interior_slope()
    analytical = result.expected["slope_v_per_mm"]
    assert measured < analytical, (
        f"expected an under-estimate; got {measured:.4e} >= {analytical:.4e} V/mm"
    )


def test_mesh_refinement_reduces_error(strip_solver):
    """Refining the mesh must move the solution toward the analytical value."""
    analytical = strip_solver(length_mm=40.0, width_mm=3.0, mesh_size=0.6).expected[
        "slope_v_per_mm"
    ]

    coarse = strip_solver(length_mm=40.0, width_mm=3.0, mesh_size=0.6)
    fine = strip_solver(length_mm=40.0, width_mm=3.0, mesh_size=0.2)

    err_coarse = abs(coarse.interior_slope() - analytical)
    err_fine = abs(fine.interior_slope() - analytical)
    assert err_fine < err_coarse, (
        f"refinement did not help: coarse err {err_coarse:.3e}, fine err {err_fine:.3e}"
    )


def test_slope_scales_linearly_with_current(strip_solver):
    """Doubling the injected current doubles the potential gradient (exact)."""
    base = strip_solver(current_a=1.0)
    doubled = strip_solver(current_a=2.0)

    ratio = doubled.interior_slope() / base.interior_slope()
    assert ratio == pytest.approx(2.0, rel=SLOPE_REL_TOL)


def test_slope_inversely_proportional_to_conductance(strip_solver):
    """Halving the sheet conductance doubles the potential gradient (exact)."""
    base = strip_solver(conductance_s=1000.0)
    halved = strip_solver(conductance_s=500.0)

    ratio = halved.interior_slope() / base.interior_slope()
    assert ratio == pytest.approx(2.0, rel=SLOPE_REL_TOL)
