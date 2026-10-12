import pyomo.environ as pyo

from param import delta_t, inv_hor, T_up
from param import SOC_min_bss, SOC_max_bss, eff_bss
from param import C_ev, P_nom_ev, eff_ev, SOC_min_ev, SOC_max_ev, SOC_target_ev
from param import PI_gen, PI_imp, PI_exp
from param import PI_c_bss, PI_c_gen, PI_c_inv, PI_c_pv
from param import C_pv_max, C_bss_max
from param import SOC_0_bss, SOC_min_bss, SOC_max_bss, eff_bss
from param import P_max_hp, COP_hp, delta_T_max, C_hp
from param import FUEL_CO2, PV_CO2, STORAGE_CO2, GENSET_CO2, INVERTER_CO2

from pyomo.environ import ConcreteModel, Param, Var, Objective, Constraint, NonNegativeReals, minimize, Binary
from datetime import datetime
import utils


def create_model(res, C_pv, C_bss, P_nom_bss, P_nom_pv, P_max_gen):
    # Create optimisation model for a fixed-size system.
    model = ConcreteModel()
    model.periods = range(res.t_s)

    # -------------------------------------------------------------------------
    # Input profiles from the Results object
    # -------------------------------------------------------------------------
    # These parameters vary at each timestep and are loaded from the input data.
    model.P_load = Param(model.periods, initialize=[res.P_load[t] for t in model.periods], mutable=True)
    model.P_pv_max = Param(model.periods, initialize=[res.P_pv_max[t] for t in model.periods], mutable=True)
    model.EV_connected = Param(model.periods, initialize=[res.EV_connected[t] for t in model.periods], mutable=True)
    model.t_arr = Param(model.periods, initialize=[res.t_arr[t] for t in model.periods], mutable=True)
    model.t_dep = Param(model.periods, initialize=[res.t_dep[t] for t in model.periods], mutable=True)
    model.SOC_i_ev = Param(model.periods, initialize=[res.SOC_i_ev[t] for t in model.periods], mutable=True)
    model.T_set = Param(model.periods, initialize=[res.T_set[t] for t in model.periods], mutable=True)
    model.P_loss = Param(model.periods, initialize=[res.P_loss[t] for t in model.periods], mutable=True)

    # Initial states for battery, EV and indoor temperature.
    model.SOC_0_bss = Param(initialize=0.5 * C_bss, mutable=True)
    model.SOC_0_ev = Param(initialize=0.5 * C_ev, mutable=True)
    model.T_0_hp = Param(initialize=res.T_set[0], mutable=True)

    # -------------------------------------------------------------------------
    # Fixed asset sizes for the planning simulation
    # -------------------------------------------------------------------------
    model.P_nom_pv = Param(initialize=P_nom_pv)       # PV inverter capacity [kW]
    model.C_bss = Param(initialize=C_bss)             # Battery capacity [kWh]
    model.C_pv = Param(initialize=C_pv)               # PV capacity [kWp]
    model.P_nom_bss = Param(initialize=P_nom_bss)     # Battery inverter capacity [kW]
    model.P_nom_ev = Param(initialize=P_nom_ev)       # EV charger capacity [kW]
    model.C_ev = Param(initialize=C_ev)               # EV battery capacity [kWh]
    model.P_max_gen = Param(initialize=P_max_gen)     # Generator capacity [kW]

    # -------------------------------------------------------------------------
    # Power flow variables
    # -------------------------------------------------------------------------
    model.P_imp = Var(model.periods, within=NonNegativeReals)              # Grid import [kW]
    model.P_exp = Var(model.periods, within=NonNegativeReals)              # Grid export [kW]
    model.P_pv = Var(model.periods, within=NonNegativeReals)               # Used PV power [kW]
    model.P_gen = Var(model.periods, within=NonNegativeReals)              # Generator output [kW]
    model.P_charge_bss = Var(model.periods, within=NonNegativeReals)       # Battery charging [kW]
    model.P_discharge_bss = Var(model.periods, within=NonNegativeReals)    # Battery discharging [kW]
    model.P_charge_ev = Var(model.periods, within=NonNegativeReals)        # EV charging [kW]
    model.P_discharge_ev = Var(model.periods, within=NonNegativeReals)     # EV discharging [kW]
    model.P_hp_hot = Var(model.periods, within=NonNegativeReals)           # Heat pump heating power [kW]
    model.P_hp_cold = Var(model.periods, within=NonNegativeReals)          # Heat pump cooling power [kW]
    model.T_hp = Var(model.periods, within=NonNegativeReals)               # Indoor temperature [°C]

    # Stored energy states.
    model.SOC_bss = Var(model.periods, within=NonNegativeReals)            # Battery energy [kWh]
    model.SOC_ev = Var(model.periods, within=NonNegativeReals)             # EV energy [kWh]

    # Binary variables used for generator status and charge/discharge exclusivity.
    model.gen_on = Var(model.periods, within=Binary)
    model.gen_startup = Var(model.periods, within=Binary)
    model.b_ch_bss = Var(model.periods, within=Binary)
    model.b_ch_ev = Var(model.periods, within=Binary)

    # =========================================================================
    # OBJECTIVE FUNCTION
    # =========================================================================
    # Minimise operating cost: import cost + generator cost - export revenue.
    model.objective = Objective(
        sense=minimize,
        expr=sum(
            delta_t * (
                PI_imp * model.P_imp[t]
                + PI_gen * model.P_gen[t]
                - PI_exp * model.P_exp[t]
            )
            for t in model.periods
        )
    )

    # =========================================================================
    # POWER BALANCE
    # =========================================================================
    # Supply must equal demand at every timestep.
    model.power_balance = Constraint(model.periods, rule=lambda m, t:
        m.P_pv[t]
        + m.P_gen[t]
        + m.P_imp[t]
        + m.P_discharge_bss[t]
        + m.P_discharge_ev[t]
        ==
        m.P_load[t]
        + m.P_hp_hot[t]
        + m.P_hp_cold[t]
        + m.P_charge_bss[t]
        + m.P_charge_ev[t]
        + m.P_exp[t]
    )

    # =========================================================================
    # PV CONSTRAINTS
    # =========================================================================
    # PV output is limited by available solar resource and inverter capacity.
    model.pv_resource_limit_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_pv[t] <= m.P_pv_max[t] * m.C_pv
    )

    model.pv_inverter_limit_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_pv[t] <= m.P_nom_pv
    )

    # =========================================================================
    # HEAT PUMP AND THERMAL COMFORT
    # =========================================================================
    # Heating and cooling power cannot exceed heat pump capacity.
    model.T_hp_max_hot_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_hp_hot[t] <= P_max_hp
    )

    model.T_hp_max_cold_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_hp_cold[t] <= P_max_hp
    )

    # Indoor temperature must remain inside the comfort band after t = 0.
    model.T_hp_lower_bound_cstr = Constraint(model.periods, rule=lambda m, t: (
        Constraint.Skip if t == 0 else
        m.T_hp[t] >= m.T_set[t] - delta_T_max
    ))

    model.T_hp_upper_bound_cstr = Constraint(model.periods, rule=lambda m, t: (
        Constraint.Skip if t == 0 else
        m.T_hp[t] <= m.T_set[t] + delta_T_max
    ))

    # Initial indoor temperature.
    model.T_hp_initial_cstr = Constraint(expr=model.T_hp[0] == model.T_0_hp)

    # Indoor temperature update based on heating/cooling and heat losses.
    model.T_hp_set_cstr = Constraint(model.periods, rule=lambda m, t:
        Constraint.Skip if t == 0 else
        m.T_hp[t] == m.T_hp[t - 1] + delta_t / C_hp * (
            COP_hp * (m.P_hp_hot[t] - m.P_hp_cold[t]) - m.P_loss[t]
        )
    )

    # =========================================================================
    # EV SOC CONSTRAINTS
    # =========================================================================
    def soc_ev_rule(m, t):
        # On arrival, reset EV SOC to the arrival value from the input data.
        if pyo.value(m.t_arr[t]) == 1:
            return m.SOC_ev[t] == m.SOC_i_ev[t]

        # If no arrival is defined at t = 0, use the default initial EV SOC.
        if t == 0:
            return m.SOC_ev[t] == m.SOC_0_ev

        # Normal EV SOC update from charging and discharging.
        return m.SOC_ev[t] == m.SOC_ev[t - 1] + delta_t * (
            eff_ev * m.P_charge_ev[t]
            - m.P_discharge_ev[t] / eff_ev
        )

    model.soc_ev_cstr = Constraint(model.periods, rule=soc_ev_rule)

    # EV SOC bounds.
    model.soc_ev_min_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.SOC_ev[t] >= SOC_min_ev * m.C_ev
    )

    model.soc_ev_max_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.SOC_ev[t] <= SOC_max_ev * m.C_ev
    )

    # EV can only charge or discharge when connected.
    model.ev_conn_ch_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_charge_ev[t] <= m.EV_connected[t] * m.P_nom_ev
    )

    model.ev_conn_dis_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_discharge_ev[t] <= m.EV_connected[t] * m.P_nom_ev
    )

    # At departure, EV must meet the target SOC.
    model.ev_departure = Constraint(
        model.periods,
        rule=lambda m, t:
            Constraint.Skip if pyo.value(m.t_dep[t]) == 0 else
            m.SOC_ev[t] >= SOC_target_ev * m.C_ev
    )

    # =========================================================================
    # BATTERY AND EV CHARGE/DISCHARGE LIMITS
    # =========================================================================
    # Binary variables prevent simultaneous charging and discharging.
    model.bss_charge_limit_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_charge_bss[t] <= m.b_ch_bss[t] * m.P_nom_bss
    )

    model.bss_discharge_limit_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_discharge_bss[t] <= (1 - m.b_ch_bss[t]) * m.P_nom_bss
    )

    model.ev_charge_limit_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_charge_ev[t] <= m.b_ch_ev[t] * m.P_nom_ev
    )

    model.ev_discharge_limit_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_discharge_ev[t] <= (1 - m.b_ch_ev[t]) * m.P_nom_ev
    )

    # =========================================================================
    # BATTERY SOC CONSTRAINTS
    # =========================================================================
    # Initial battery stored energy.
    model.soc_bss_start_cstr = Constraint(expr=model.SOC_bss[0] == model.SOC_0_bss)

    # Battery SOC bounds.
    model.soc_bss_min_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.SOC_bss[t] >= SOC_min_bss * m.C_bss
    )

    model.soc_bss_max_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.SOC_bss[t] <= SOC_max_bss * m.C_bss
    )

    # Battery SOC update from charging and discharging.
    model.soc_bss_cstr = Constraint(model.periods, rule=lambda m, t:
        Constraint.Skip if t == 0 else
        m.SOC_bss[t] == m.SOC_bss[t - 1] + delta_t * (
            eff_bss * m.P_charge_bss[t]
            - m.P_discharge_bss[t] / eff_bss
        )
    )

    # =========================================================================
    # GENERATOR CONSTRAINTS
    # =========================================================================
    # Generator output is zero when off and limited by its fixed capacity when on.
    model.P_gen_lower_bound_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_gen[t] >= 0
    )

    model.P_gen_upper_bound_cstr = Constraint(
        model.periods,
        rule=lambda m, t: m.P_gen[t] <= m.gen_on[t] * m.P_max_gen
    )

    # Startup variable is activated when generator status changes from off to on.
    model.gen_startup_cstr = Constraint(model.periods, rule=lambda m, t:
        Constraint.Skip if t == 0 else
        m.gen_startup[t] >= m.gen_on[t] - m.gen_on[t - 1]
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
        utils.export_results(results, "planning")

    # Main full-horizon dispatch plot.
    utils.plot_res(results)

    # Post-processing price sensitivity using the same dispatch.
    utils.plot_price_sensitivity(results)

    # Representative-day plots for selected operating conditions
    # (five distinct days are picked, so the horizon needs at least five days).
    if len(results.datetime) >= 5 * 96:
        utils.plot_representative_days(results)
    else:
        print("Horizon shorter than 5 days: representative-day plots skipped.")

    return results


if __name__ == "__main__":
    start_time = datetime(2021, 1, 1, 0, 0, 0)
    import sys
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 365

    # Fixed system sizes used in the planning simulation.
    C_pv = 10
    C_bss = 40
    P_nom_bss = 10
    P_nom_pv = 10
    P_max_gen = 10

    results = utils.Results(
        start_time,
        n_days,
        yearly_kwh=10000,
        yearly_km=20000
    )

    model = create_model(results, C_pv, C_bss, P_nom_bss, P_nom_pv, P_max_gen)
    run(model, results)