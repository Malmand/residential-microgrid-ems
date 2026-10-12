from datetime import datetime

from pyomo.environ import (
    ConcreteModel,
    Param,
    Var,
    Objective,
    Constraint,
    NonNegativeReals,
    minimize,
    value,
)

from param import delta_t, C_pv_max, C_bss_max
from param import SOC_min_bss, SOC_max_bss, eff_bss
from param import C_ev, P_nom_ev, eff_ev, SOC_min_ev, SOC_max_ev, SOC_target_ev
from param import P_max_hp, COP_hp, delta_T_max, C_hp
from param import inv_hor, PI_c_pv, PI_c_bss, PI_c_inv, PI_c_gen
from param import PI_gen, PI_imp, PI_exp

import utils


# =============================================================================
# CASE SETTINGS
# =============================================================================
# Choose one case:
# "base", "isolated", "no_export_payment", or "capex_budget"

CASE_NAME = "base"
P_GRID_MAX = 100.0

# Set CAPEX_BUDGET only when CASE_NAME = "capex_budget".
# For all other cases, set CAPEX_BUDGET = None.
CAPEX_BUDGET = None

def create_model(res):
    # =========================================================================
    # MODEL
    # =========================================================================
    model = ConcreteModel()
    model.periods = range(res.t_s)

    # =========================================================================
    # INPUT DATA
    # =========================================================================
    # Time-dependent input profiles loaded from the Results object.
    model.P_load = Param(
        model.periods,
        initialize=[res.P_load[t] for t in model.periods],
        mutable=True,
    )

    model.P_pv_max = Param(
        model.periods,
        initialize=[res.P_pv_max[t] for t in model.periods],
        mutable=True,
    )

    model.EV_connected = Param(
        model.periods,
        initialize=[res.EV_connected[t] for t in model.periods],
        mutable=True,
    )

    model.t_arr = Param(
        model.periods,
        initialize=[res.t_arr[t] for t in model.periods],
        mutable=True,
    )

    model.t_dep = Param(
        model.periods,
        initialize=[res.t_dep[t] for t in model.periods],
        mutable=True,
    )

    model.SOC_i_ev = Param(
        model.periods,
        initialize=[res.SOC_i_ev[t] for t in model.periods],
        mutable=True,
    )

    model.T_set = Param(
        model.periods,
        initialize=[res.T_set[t] for t in model.periods],
        mutable=True,
    )

    model.P_loss = Param(
        model.periods,
        initialize=[res.P_loss[t] for t in model.periods],
        mutable=True,
    )

    # Initial states.
    model.SOC_0_bss = Param(initialize=0.5, mutable=True)
    model.SOC_0_ev = Param(initialize=0.5 * C_ev, mutable=True)
    model.T_0_hp = Param(initialize=res.T_set[0], mutable=True)

    # =========================================================================
    # SIZING VARIABLES
    # =========================================================================
    # These asset sizes are selected by the optimiser.
    model.C_pv = Var(bounds=(0, C_pv_max))
    model.P_nom_pv = Var(within=NonNegativeReals)

    model.C_bss = Var(bounds=(0, C_bss_max))
    model.P_nom_bss = Var(within=NonNegativeReals)

    model.P_max_gen = Var(within=NonNegativeReals)

    # Fixed assets.
    model.C_ev = Param(initialize=C_ev)
    model.P_nom_ev = Param(initialize=P_nom_ev)
    model.P_nom_hp = Param(initialize=P_max_hp)

    # =========================================================================
    # OPERATION VARIABLES
    # =========================================================================
    model.P_imp = Var(model.periods, within=NonNegativeReals)
    model.P_exp = Var(model.periods, within=NonNegativeReals)

    model.P_pv = Var(model.periods, within=NonNegativeReals)
    model.P_gen = Var(model.periods, within=NonNegativeReals)

    model.P_charge_bss = Var(model.periods, within=NonNegativeReals)
    model.P_discharge_bss = Var(model.periods, within=NonNegativeReals)

    model.P_charge_ev = Var(model.periods, within=NonNegativeReals)
    model.P_discharge_ev = Var(model.periods, within=NonNegativeReals)

    model.P_hp_hot = Var(model.periods, within=NonNegativeReals)
    model.P_hp_cold = Var(model.periods, within=NonNegativeReals)

    model.T_hp = Var(model.periods, within=NonNegativeReals)

    model.SOC_bss = Var(model.periods, within=NonNegativeReals)
    model.SOC_ev = Var(model.periods, within=NonNegativeReals)

    # =========================================================================
    # CAPEX AND OPEX EXPRESSIONS
    # =========================================================================
    # CAPEX is scaled to the simulated period.
    total_hours = len(model.periods) * delta_t
    scaling_factor = total_hours / (8760 * inv_hor)

    capex_expr = (
        PI_c_pv * model.C_pv
        + PI_c_bss * model.C_bss
        + PI_c_inv * (model.P_nom_pv + model.P_nom_bss)
        + PI_c_gen * model.P_max_gen
    )

    # Export earns no revenue only in the no-export-payment case.
    export_price = 0.0 if CASE_NAME == "no_export_payment" else PI_exp

    # Store case information for result calculations in utils.py.
    model.export_price_used = export_price
    model.case_name = CASE_NAME

    opex_expr = sum(
        delta_t
        * (
            PI_imp * model.P_imp[t]
            + PI_gen * model.P_gen[t]
            - export_price * model.P_exp[t]
        )
        for t in model.periods
    )

    # =========================================================================
    # OBJECTIVE FUNCTION
    # =========================================================================
    # Minimise scaled CAPEX plus operating cost.
    model.objective = Objective(
        sense=minimize,
        expr=scaling_factor * capex_expr + opex_expr,
    )

    # =========================================================================
    # OPTIONAL CAPEX BUDGET CONSTRAINT
    # =========================================================================
    if CASE_NAME == "capex_budget" and CAPEX_BUDGET is not None:
        model.capex_budget_limit = Constraint(
            expr=capex_expr <= CAPEX_BUDGET
        )

    # =========================================================================
    # POWER BALANCE
    # =========================================================================
    # Supply must equal demand at every timestep.
    def power_balance_rule(m, t):
        return (
            m.P_imp[t]
            + m.P_pv[t]
            + m.P_gen[t]
            + m.P_discharge_bss[t]
            + m.P_discharge_ev[t]
            ==
            m.P_load[t]
            + m.P_exp[t]
            + m.P_charge_bss[t]
            + m.P_charge_ev[t]
            + m.P_hp_hot[t]
            + m.P_hp_cold[t]
        )

    model.power_balance = Constraint(model.periods, rule=power_balance_rule)

    # =========================================================================
    # GRID CONSTRAINTS
    # =========================================================================
    if CASE_NAME == "isolated":
        # Isolated case: no grid import or export.
        model.grid_import_limit = Constraint(
            model.periods,
            rule=lambda m, t: m.P_imp[t] == 0.0,
        )

        model.grid_export_limit = Constraint(
            model.periods,
            rule=lambda m, t: m.P_exp[t] == 0.0,
        )
    else:
        # Grid-connected cases.
        model.grid_import_limit = Constraint(
            model.periods,
            rule=lambda m, t: m.P_imp[t] <= P_GRID_MAX,
        )

        model.grid_export_limit = Constraint(
            model.periods,
            rule=lambda m, t: m.P_exp[t] <= P_GRID_MAX,
        )

    # =========================================================================
    # PV CONSTRAINTS
    # =========================================================================
    # PV output is limited by available solar resource and inverter capacity.
    model.pv_resource_limit = Constraint(
        model.periods,
        rule=lambda m, t: m.P_pv[t] <= m.P_pv_max[t] * m.C_pv,
    )

    model.pv_inverter_limit = Constraint(
        model.periods,
        rule=lambda m, t: m.P_pv[t] <= m.P_nom_pv,
    )

    # Prevent PV inverter oversizing above installed PV capacity.
    model.pv_inverter_size_limit = Constraint(
        expr=model.P_nom_pv <= model.C_pv
    )

    # =========================================================================
    # BATTERY CONSTRAINTS
    # =========================================================================
    def soc_bss_rule(m, t):
        # Initial battery energy is a fraction of selected capacity.
        if t == 0:
            return m.SOC_bss[t] == m.SOC_0_bss * m.C_bss

        # Battery SOC update.
        return m.SOC_bss[t] == m.SOC_bss[t - 1] + delta_t * (
            eff_bss * m.P_charge_bss[t]
            - m.P_discharge_bss[t] / eff_bss
        )

    model.soc_bss_cons = Constraint(model.periods, rule=soc_bss_rule)

    # Battery SOC bounds.
    model.soc_bss_min = Constraint(
        model.periods,
        rule=lambda m, t: m.SOC_bss[t] >= SOC_min_bss * m.C_bss,
    )

    model.soc_bss_max = Constraint(
        model.periods,
        rule=lambda m, t: m.SOC_bss[t] <= SOC_max_bss * m.C_bss,
    )

    # Battery power is limited by selected inverter capacity.
    model.bss_charge_limit = Constraint(
        model.periods,
        rule=lambda m, t: m.P_charge_bss[t] <= m.P_nom_bss,
    )

    model.bss_discharge_limit = Constraint(
        model.periods,
        rule=lambda m, t: m.P_discharge_bss[t] <= m.P_nom_bss,
    )

    # Practical linear relation between battery energy and inverter size.
    model.bss_inverter_size_limit = Constraint(
        expr=model.P_nom_bss <= model.C_bss / delta_t
    )

    # =========================================================================
    # EV CONSTRAINTS
    # =========================================================================
    def soc_ev_rule(m, t):
        # On EV arrival, reset SOC to arrival SOC from input data.
        if value(m.t_arr[t]) == 1:
            return m.SOC_ev[t] == m.SOC_i_ev[t]

        # If no arrival is defined at t = 0, use default initial EV SOC.
        if t == 0:
            return m.SOC_ev[t] == m.SOC_0_ev

        # Normal EV SOC update.
        return m.SOC_ev[t] == m.SOC_ev[t - 1] + delta_t * (
            eff_ev * m.P_charge_ev[t]
            - m.P_discharge_ev[t] / eff_ev
        )

    model.soc_ev_cons = Constraint(model.periods, rule=soc_ev_rule)

    # EV SOC bounds.
    model.soc_ev_min = Constraint(
        model.periods,
        rule=lambda m, t: m.SOC_ev[t] >= SOC_min_ev * m.C_ev,
    )

    model.soc_ev_max = Constraint(
        model.periods,
        rule=lambda m, t: m.SOC_ev[t] <= SOC_max_ev * m.C_ev,
    )

    # EV can only charge or discharge when connected.
    model.ev_conn_charge = Constraint(
        model.periods,
        rule=lambda m, t: m.P_charge_ev[t] <= m.EV_connected[t] * m.P_nom_ev,
    )

    model.ev_conn_discharge = Constraint(
        model.periods,
        rule=lambda m, t: m.P_discharge_ev[t] <= m.EV_connected[t] * m.P_nom_ev,
    )

    # At departure, EV must meet the target SOC.
    def ev_departure_rule(m, t):
        if value(m.t_dep[t]) == 0:
            return Constraint.Skip

        return m.SOC_ev[t] >= SOC_target_ev * m.C_ev

    model.ev_departure = Constraint(model.periods, rule=ev_departure_rule)

    # =========================================================================
    # HEAT PUMP AND THERMAL COMFORT
    # =========================================================================
    # Initial indoor temperature.
    model.T_hp_initial = Constraint(
        expr=model.T_hp[0] == model.T_0_hp
    )

    def thermal_balance_rule(m, t):
        if t == 0:
            return Constraint.Skip

        # Indoor temperature update from heat pump and building losses.
        return m.T_hp[t] == m.T_hp[t - 1] + (delta_t / C_hp) * (
            COP_hp * (m.P_hp_hot[t] - m.P_hp_cold[t])
            - m.P_loss[t]
        )

    model.thermal_balance = Constraint(model.periods, rule=thermal_balance_rule)

    # Comfort limits are enforced after the initial state.
    model.temperature_lower = Constraint(
        model.periods,
        rule=lambda m, t:
            Constraint.Skip if t == 0 else
            m.T_hp[t] >= m.T_set[t] - delta_T_max,
    )

    model.temperature_upper = Constraint(
        model.periods,
        rule=lambda m, t:
            Constraint.Skip if t == 0 else
            m.T_hp[t] <= m.T_set[t] + delta_T_max,
    )

    # Heat pump power limits.
    model.hp_hot_limit = Constraint(
        model.periods,
        rule=lambda m, t: m.P_hp_hot[t] <= m.P_nom_hp,
    )

    model.hp_cold_limit = Constraint(
        model.periods,
        rule=lambda m, t: m.P_hp_cold[t] <= m.P_nom_hp,
    )

    # =========================================================================
    # GENERATOR LIMIT
    # =========================================================================
    model.gen_limit = Constraint(
        model.periods,
        rule=lambda m, t: m.P_gen[t] <= m.P_max_gen,
    )

    # Index 0 only carries the initial state: storage power there would not
    # change any SOC, so it is fixed to zero (no free energy at the start).
    for v in (model.P_charge_bss, model.P_discharge_bss,
              model.P_charge_ev, model.P_discharge_ev):
        v[0].fix(0.0)

    return model


def run(model, results):
    model, results = utils.solve_model(model, results)

    if model and results:
        utils.check_res(results)
        utils.print_res(results)
        utils.print_sizing_results(results)
        utils.export_results(results, f"sizing_{CASE_NAME}", sizing=True)

        # Full-horizon dispatch plot.
        utils.plot_res(results)

        # Representative-day plots for report discussion (needs at least 5 days).
        if len(results.datetime) >= 5 * 96:
            utils.plot_representative_days(results)

    return results


if __name__ == "__main__":
    start_time = datetime(2021, 1, 1, 0, 0, 0)

    # Use shorter runs for testing; use 365 days for final results.
    import sys
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 365

    results = utils.Results(
        start_time,
        n_days,
        yearly_kwh=10000,
        yearly_km=20000,
    )

    model = create_model(results)
    run(model, results)