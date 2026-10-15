"""
Optimization-Based Energy Management System (EMS)
===================================================================

This module implements a model-based controller for a residential
microgrid using mixed-integer optimization. At each timestep, the
controller computes optimal power setpoints for all system components
by solving a Pyomo model with the HiGHS solver.

Requirements:
    pip install pyomo highspy


Overview
--------
The controller determines the optimal dispatch of:
    - Photovoltaic generation (PV)
    - Diesel generator (GEN)
    - Battery storage system (BSS)
    - Electric vehicle (EV)
    - Heat pump (HP)

Decisions are computed simultaneously to satisfy demand, respect
physical constraints, and minimize operational cost.


Controller Structure
--------------------
The implementation follows a standard optimization workflow:
    - create_model(): builds the Pyomo model once
    - update_model(): updates time-dependent parameters and bounds
    - control_decision(): solves the model at each timestep and returns setpoints


Modeling Approach
-----------------
Unlike rule-based control, which applies decisions sequentially,
this controller:
    - optimizes all variables jointly at each timestep
    - uses cost weights to encode operational priorities
    - enforces only physical feasibility constraints


Physical Constraints
--------------------
The model enforces:
    - Power balance
    - PV generation limits
    - Generator ON/OFF behavior using a binary variable
    - Battery charge/discharge mutual exclusivity
    - EV charge/discharge mutual exclusivity
    - SOC limits for battery and EV
    - SOC-based power limits updated each timestep
    - EV discharge only above a minimum SOC threshold


Objective Function
------------------
The objective minimizes weighted operational cost, including:
    - Load shedding
    - Generator fuel use
    - EV discharge
    - Battery discharge
    - PV curtailment
    - EV charging shortfall
    - Small cycling wear penalty


Design Choice
-------------
The heat pump is controlled using the same rule-based logic as the
baseline controller. This keeps the thermal control identical and
isolates the optimization improvement to the electrical dispatch.

Additional Tool
---------------
The script compare_cost.py can be used to compare virtual and real
operational costs across all scenarios and control strategies.

"""
import pyomo.environ as pyo
from pyomo.opt import SolverFactory

from param import delta_t, time_steps
from param import (
    P_nom_pv, P_max_gen, P_min_gen,
    C_bss, SOC_min_bss, SOC_max_bss, eff_bss, P_nom_bss,
    COP_hp, C_hp,
)
from param import (
    C_ev, eff_ev, SOC_min_ev, SOC_max_ev,
    P_nom_ev, SOC_target_ev, P_max_hp,
)
import simulator as run

# =============================================================================
# SCENARIO SELECTION
# =============================================================================
Scenario = 'S4'   # Default scenario; override from the command line: python milp_controller.py S1

# =============================================================================
# OBJECTIVE WEIGHTS
# =============================================================================
# Weights define the operational priority order used by the optimizer.
W_SHED    = 1000000.0  # Load shedding is strictly impossible if Gen can run
W_GEN     =   20.0     # Generator fuel cost is EXPENSIVE
W_EV_DIS  =    6.0     # V2H discharge is allowed but discouraged
W_BSS_DIS =    1.0     # Battery discharge is the cheapest storage
W_PV      =    2.0     # Penalise curtailment of free PV
W_EV_MISS =    8.0     # LOWER THAN W_GEN: Let the EV miss its target rather than burn fuel
W_WEAR    =    0.02    # Cycling wear

# =============================================================================
# CONSTANTS
# =============================================================================
EPS          = 1e-9
EV_SOC_FLOOR = 0.5   # mirrors RB ev_discharge_limit(soc_floor=0.5)

# =============================================================================
# RULE-BASED HP  (identical to RB.py)
# =============================================================================
TEMP_DEADBAND  = 0.5
HP_GAIN        = 0.7
SMALL_ERR_BAND = 1.0
SMALL_ERR_GAIN = 0.5


def clamp(x, xmin, xmax):
    return max(xmin, min(x, xmax))


def hp_power(T_set, T_hp, P_loss):
    if T_hp >= T_set - TEMP_DEADBAND:
        return 0.0
    p_req = ((((T_set - T_hp) * C_hp) / delta_t) + P_loss) / COP_hp  # electric power to reach T_set in one step
    p_req *= HP_GAIN
    if 0.0 < (T_set - T_hp) < SMALL_ERR_BAND:
        p_req *= SMALL_ERR_GAIN
    return clamp(p_req, 0.0, P_max_hp)


# =============================================================================
# CREATE OPTIMIZATION MODEL
# =============================================================================

def create_model():
    m = pyo.ConcreteModel()

    # ── Mutable parameters (refreshed every timestep in update_model) ─────────
    m.total_load   = pyo.Param(mutable=True, initialize=0.0)
    m.P_pv_max     = pyo.Param(mutable=True, initialize=0.0)
    m.EV_connected = pyo.Param(mutable=True, initialize=0, within=pyo.Binary)
    m.EV_remaining = pyo.Param(mutable=True, initialize=EPS)
    m.SOC_ev_now   = pyo.Param(mutable=True, initialize=0.5)

    # ── Decision variables ────────────────────────────────────────────────────
    m.P_pv = pyo.Var(bounds=(0, P_nom_pv))

    # Generator: MILP binary + continuous output
    m.Gen_ON = pyo.Var(domain=pyo.Binary)
    m.P_gen  = pyo.Var(bounds=(0, P_max_gen))

    # Battery
    m.P_ch_bss  = pyo.Var(bounds=(0, P_nom_bss))
    m.P_dis_bss = pyo.Var(bounds=(0, P_nom_bss))
    m.BSS_CH    = pyo.Var(domain=pyo.Binary)   # 1 = charging mode

    # EV
    m.P_ch_ev  = pyo.Var(bounds=(0, P_nom_ev))
    m.P_dis_ev = pyo.Var(bounds=(0, P_nom_ev))
    m.EV_CH    = pyo.Var(domain=pyo.Binary)    # 1 = charging mode

    # Slack variables
    m.P_shed  = pyo.Var(bounds=(0, None))   # load shedding [kW]
    m.EV_miss = pyo.Var(bounds=(0, None))   # unmet EV charge rate [kW]

    # ── Objective ─────────────────────────────────────────────────────────────
    def obj_rule(m):
        curtail = m.P_pv_max - m.P_pv
        return (
              W_SHED    * m.P_shed
            + W_GEN     * m.P_gen
            + W_EV_DIS  * m.P_dis_ev
            + W_BSS_DIS * m.P_dis_bss
            + W_PV      * curtail
            + W_EV_MISS * m.EV_miss
            + W_WEAR    * (m.P_ch_bss + m.P_dis_bss + m.P_ch_ev + m.P_dis_ev)
        )

    m.obj = pyo.Objective(rule=obj_rule, sense=pyo.minimize)

    # ── Power balance constraint ───────────────────────────────────────────────
    def balance_rule(m):
        supply = m.P_pv + m.P_gen + m.P_dis_bss + m.P_dis_ev + m.P_shed
        demand = m.total_load + m.P_ch_bss + m.P_ch_ev
        return supply == demand

    m.P_bal_cstr = pyo.Constraint(rule=balance_rule)

    # ── PV constraint: cannot exceed available resource ───────────────────────
    m.pv_cap = pyo.Constraint(expr=m.P_pv <= m.P_pv_max)

    # ── Generator MILP constraints ────────────────────────────────────────────
    m.gen_max = pyo.Constraint(expr=m.P_gen <= P_max_gen * m.Gen_ON)
    m.gen_min = pyo.Constraint(expr=m.P_gen >= P_min_gen * m.Gen_ON)

    # ── Battery mutual exclusion ──────────────────────────────────────────────
    m.bss_mutex_ch  = pyo.Constraint(expr=m.P_ch_bss  <= P_nom_bss * m.BSS_CH)
    m.bss_mutex_dis = pyo.Constraint(expr=m.P_dis_bss <= P_nom_bss * (1 - m.BSS_CH))

    # ── EV mutual exclusion ───────────────────────────────────────────────────
    m.ev_mutex_ch  = pyo.Constraint(expr=m.P_ch_ev  <= P_nom_ev * m.EV_CH)
    m.ev_mutex_dis = pyo.Constraint(expr=m.P_dis_ev <= P_nom_ev * (1 - m.EV_CH))

    # ── EV soft SOC target ────────────────────────────────────────────────────
    def ev_miss_rule(m):
        ideal_rate = (
            m.EV_connected
            * ((SOC_target_ev - m.SOC_ev_now) * C_ev / eff_ev)
            / m.EV_remaining
        )
        return m.EV_miss >= ideal_rate - m.P_ch_ev

    m.ev_miss_con = pyo.Constraint(rule=ev_miss_rule)

    return m


# =============================================================================
# UPDATE TIME-DEPENDENT PARAMETERS
# =============================================================================

def update_model(model, total_load, P_pv_max,
                 EV_connected, EV_remaining_time, SOC_bss, SOC_ev):
    model.total_load.set_value(total_load)
    model.P_pv_max.set_value(P_pv_max)
    model.EV_connected.set_value(int(EV_connected))
    model.EV_remaining.set_value(max(EV_remaining_time, EPS))
    model.SOC_ev_now.set_value(SOC_ev)

    # ── Battery bounds ─────────────────────────────────────────────────────
    free_bss    = max(0.0, (SOC_max_bss - SOC_bss) * C_bss)
    max_ch_bss  = min(P_nom_bss, free_bss / (eff_bss * delta_t))  # power that fills the battery in one step

    avail_bss   = max(0.0, (SOC_bss - SOC_min_bss) * C_bss)
    max_dis_bss = min(P_nom_bss, avail_bss * eff_bss / delta_t)  # power that empties it in one step

    model.P_ch_bss.setub(max_ch_bss)
    model.P_dis_bss.setub(max_dis_bss)

    # ── EV bounds ──────────────────────────────────────────────────────────
    if EV_connected:
        free_ev   = max(0.0, (SOC_max_ev - SOC_ev) * C_ev)
        max_ch_ev = min(P_nom_ev, free_ev / (eff_ev * delta_t))
        model.P_ch_ev.setub(max_ch_ev)
        model.P_ch_ev.setlb(0.0)

        soc_min_v2h = max(SOC_min_ev, EV_SOC_FLOOR)  # V2H never below 50 %
        avail_ev    = max(0.0, (SOC_ev - soc_min_v2h) * C_ev)
        max_dis_ev  = min(P_nom_ev, avail_ev * eff_ev / delta_t)
        model.P_dis_ev.setub(max_dis_ev)
        model.P_dis_ev.setlb(0.0)
    else:
        model.P_ch_ev.setlb(0.0);  model.P_ch_ev.setub(0.0)
        model.P_dis_ev.setlb(0.0); model.P_dis_ev.setub(0.0)


# =============================================================================
# CONTROL DECISION INTERFACE
# =============================================================================

_model  = None
_solver = None


def control_decision(
    P_load, P_pv_max, EV_connected, EV_remaining_time,
    P_gen_prev, SOC_bss, SOC_ev, P_loss, T_set, T_hp,
    model=None,
):
    global _model, _solver

    if _model is None:
        _model  = create_model()
        _solver = SolverFactory('appsi_highs')

    # HEAT PUMP CONTROL
    # Same logic as the RB controller to keep thermal control comparable.
    P_hp       = hp_power(T_set, T_hp, P_loss)
    total_load = P_load + P_hp

    # ── Step 2: Update model parameters and bounds ────────────────────────
    update_model(_model, total_load, P_pv_max,
                 EV_connected, EV_remaining_time, SOC_bss, SOC_ev)

    # ── Step 3: Solve MILP ────────────────────────────────────────────────
    _solver.solve(_model, tee=False)

    # ── Step 4: Extract results ───────────────────────────────────────────
    def v(var):
        return pyo.value(var)

    P_pv      = clamp(v(_model.P_pv),    0.0, P_pv_max)
    P_ch_bss  = max(0.0, v(_model.P_ch_bss))
    P_dis_bss = max(0.0, v(_model.P_dis_bss))
    P_ch_ev   = max(0.0, v(_model.P_ch_ev))  if EV_connected else 0.0
    P_dis_ev  = max(0.0, v(_model.P_dis_ev)) if EV_connected else 0.0

    # ── Step 5: Reduce HP demand if load shedding is required ─
    P_shed = max(0.0, v(_model.P_shed))
    if P_shed > EPS:
        P_hp -= min(P_hp, P_shed)  # shed heat pump first

    # ── Step 5.5: Numerical power-balance correction ──────────────────────
    # The optimization model enforces power balance internally. After value
    # extraction and numerical cleaning, small residuals may remain. Therefore,
    # P_gen is recomputed from the returned balance to ensure consistency with
    # utils.check_res().
    demand = P_load + P_hp + P_ch_bss + P_ch_ev
    P_gen  = demand - P_pv - P_dis_bss - P_dis_ev
    P_gen  = clamp(P_gen, 0.0, P_max_gen)

    # ── Step 6: Build signed outputs and clean numerical noise ────────────
    P_bss = P_ch_bss - P_dis_bss   # positive = charge, negative = discharge
    P_ev  = P_ch_ev  - P_dis_ev    # positive = charge, negative = V2H

    def clean(x):
        return x if abs(x) > EPS else 0.0

    return (
        clean(P_pv),
        clean(P_gen),
        clean(P_bss),
        clean(P_ev),
        clean(P_hp),
    )


# =============================================================================
# RUN SIMULATION
# =============================================================================
if __name__ == '__main__':
    import os, sys
    import plotting as utils

    Scenario = sys.argv[1] if len(sys.argv) > 1 else Scenario
    results = run.run_sim(control_decision, Scenario)
    fig_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'figures')
    utils.plot_res(results, scenario=f'{Scenario}_OPT', save_dir=fig_dir)

    import numpy as np
    from param import delta_t

    # ── Summary statistics ─────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Scenario: {Scenario}  |  Controller: OPT")
    print(f"{'='*60}")
    print(f"PV available [kWh]: {results.P_pv_max.sum() * delta_t:.2f}")
    print(f"PV used      [kWh]: {results.P_pv.sum() * delta_t:.2f}")
    print(f"PV curtailed [kWh]: {(results.P_pv_max - results.P_pv).clip(0).sum() * delta_t:.2f}")
    print(f"Generator    [kWh]: {results.P_gen.sum() * delta_t:.2f}")
    print(f"T_hp min / max [°C]: {results.T_hp.min():.2f} / {results.T_hp.max():.2f}")
    print(f"P_hp total   [kWh]: {results.P_hp.sum() * delta_t:.2f}")
    print(f"Battery final SOC  : {results.SOC_bss[-1]:.3f}")
    print(f"EV final SOC       : {results.SOC_ev[-1]:.3f}")
    print(f"BSS power min/max  : {results.P_bss.min():.2f} / {results.P_bss.max():.2f}")
    print(f"EV  power min/max  : {results.P_ev.min():.2f} / {results.P_ev.max():.2f}")

    # ── Cost breakdown ─────────────────────────────────────────────────────
    try:
        utils.print_costs(results, delta_t, fuel_price=0.3)
    except AttributeError:
        pass  # print_costs may not exist in all versions of utils.py
