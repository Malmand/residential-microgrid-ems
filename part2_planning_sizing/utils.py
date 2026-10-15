import os
from pyomo.environ import SolverFactory, SolverStatus, value
from datetime import timedelta
import pandas as pd
import numpy as np
from param import *

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, '..', 'data', 'annual', 'annual_profiles_2021.csv')
FIG_DIR = os.path.join(HERE, 'figures')
os.makedirs(FIG_DIR, exist_ok=True)


# Journal figure style: L-frame axes, outward ticks, no grid, Okabe-Ito colours.
OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#56B4E9",
             "#CC79A7", "#F0E442", "#000000"]


def journal_style():
    import matplotlib.pyplot as plt
    from cycler import cycler
    plt.rcdefaults()
    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": 9,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.linewidth": 0.8, "axes.grid": False,
        "axes.prop_cycle": cycler(color=OKABE_ITO),
        "xtick.direction": "out", "ytick.direction": "out",
        "xtick.major.width": 0.8, "ytick.major.width": 0.8,
        "legend.frameon": False, "legend.fontsize": 8,
        "lines.linewidth": 1.1,
    })


# =============================================================================
# RESULT CHECKING
# =============================================================================

def check_res(res):
    # Small tolerance to avoid flagging numerical solver noise.
    eps = 1e-3
    violations = []

    for t in range(len(res.t)):
        # Reconstruct supply and demand from saved results.
        supply = (
            res.P_pv[t]
            + res.P_gen[t]
            + res.P_imp[t]
            + max(-res.P_bss[t], 0.0)
            + max(-res.P_ev[t], 0.0)
        )

        demand = (
            res.P_load[t]
            + res.P_hp_hot[t]
            + res.P_hp_cold[t]
            + max(res.P_bss[t], 0.0)
            + max(res.P_ev[t], 0.0)
            + res.P_exp[t]
        )

        if abs(supply - demand) > eps:
            violations.append(
                f"Power balance error at t={t}: {supply - demand:.4f} kW"
            )

        # These variables should never be negative.
        for name in ["P_pv", "P_gen", "P_imp", "P_exp", "P_hp_hot", "P_hp_cold"]:
            if getattr(res, name)[t] < -eps:
                violations.append(
                    f"{name} negative at t={t}: {getattr(res, name)[t]:.4f}"
                )

    if len(violations) == 0:
        print("✅ Constraint check passed")
    else:
        print(f"❌ Constraint check failed: {len(violations)} violation(s)")
        for v in violations[:20]:
            print(" ", v)

    return


# =============================================================================
# PRINTING FUNCTIONS
# =============================================================================

def print_res(res):
    print("\n" + "=" * 60)
    print("MICROGRID SIMULATION SUMMARY")
    print("=" * 60)

    print(f"Simulation length       : {res.n_days} day(s)")
    print(f"Number of timesteps     : {res.t_s}")
    print(f"Objective value         : {res.objective:.2f}")
    print(f"Total CAPEX             : {res.capex_total:.2f} €")
    print(f"Scaled CAPEX in obj.    : {res.capex_scaled:.2f} €")
    print(f"OPEX simulated period   : {res.opex_period:.2f} €")
    print(f"OPEX one year           : {res.opex_year:.2f} €/year")
    print("-" * 60)
    print(f"Load demand             : {res.P_load.sum() * delta_t:.2f} kWh")
    print(f"PV used                 : {res.P_pv.sum() * delta_t:.2f} kWh")
    print(f"Generator energy        : {res.P_gen.sum() * delta_t:.2f} kWh")
    print(f"Grid import             : {res.P_imp.sum() * delta_t:.2f} kWh")
    print(f"Grid export             : {res.P_exp.sum() * delta_t:.2f} kWh")
    print("-" * 60)
    print(f"Battery final SOC       : {res.SOC_bss[-1]:.2f} kWh")
    print(f"EV final SOC            : {res.SOC_ev[-1]:.2f} kWh")
    print(f"Indoor temp min/max     : {res.T_hp.min():.2f} / {res.T_hp.max():.2f} °C")
    print(f"HP heating energy       : {res.P_hp_hot.sum() * delta_t:.2f} kWh")
    print(f"HP cooling energy       : {res.P_hp_cold.sum() * delta_t:.2f} kWh")
    print(f"EV net energy           : {res.P_ev.sum() * delta_t:.2f} kWh")
    print(f"Battery net energy      : {res.P_bss.sum() * delta_t:.2f} kWh")
    print("=" * 60)

    return


def export_results(res, name, sizing=False):
    """Write a one-row summary and the 15-min time series to ../results as CSV."""
    out_dir = os.path.join(HERE, "..", "results")
    os.makedirs(out_dir, exist_ok=True)
    e = lambda p: float(np.sum(p) * delta_t)
    summary = {
        "days": res.n_days,
        "objective_EUR": res.objective,
        "opex_period_EUR": res.opex_period,
        "opex_year_EUR": res.opex_year,
        "capex_total_EUR": res.capex_total,
        "load_kWh": e(res.P_load), "pv_used_kWh": e(res.P_pv),
        "generator_kWh": e(res.P_gen), "grid_import_kWh": e(res.P_imp),
        "grid_export_kWh": e(res.P_exp), "hp_heating_kWh": e(res.P_hp_hot),
        "hp_cooling_kWh": e(res.P_hp_cold),
        "indoor_T_min_C": float(res.T_hp.min()), "indoor_T_max_C": float(res.T_hp.max()),
    }
    if sizing:
        summary.update({"C_pv_kWp": res.C_pv, "P_nom_pv_kW": res.P_nom_pv,
                        "C_bss_kWh": res.C_bss, "P_nom_bss_kW": res.P_nom_bss,
                        "P_max_gen_kW": res.P_max_gen})
    pd.DataFrame([summary]).to_csv(os.path.join(out_dir, f"part2_{name}_summary.csv"),
                                   index=False, float_format="%.3f")
    ts = pd.DataFrame({
        "datetime": res.datetime[:len(res.P_imp)],
        "load_kW": res.P_load[:len(res.P_imp)], "pv_kW": res.P_pv,
        "grid_import_kW": res.P_imp, "grid_export_kW": res.P_exp,
        "generator_kW": res.P_gen, "battery_kW (+charge)": res.P_bss,
        "ev_kW (+charge)": res.P_ev, "hp_heating_kW": res.P_hp_hot,
        "hp_cooling_kW": res.P_hp_cold, "soc_battery_kWh": res.SOC_bss,
        "soc_ev_kWh": res.SOC_ev, "indoor_T_C": res.T_hp,
    })
    ts.to_csv(os.path.join(out_dir, f"part2_{name}_timeseries.csv"), index=False,
              float_format="%.4f")
    print(f"Saved results/part2_{name}_summary.csv and _timeseries.csv")


def print_sizing_results(res):
    print("\n" + "=" * 60)
    print("SIZING RESULTS")
    print("=" * 60)

    print(f"PV capacity             : {res.C_pv:.2f} kWp")
    print(f"PV inverter power       : {res.P_nom_pv:.2f} kW")
    print(f"Battery capacity        : {res.C_bss:.2f} kWh")
    print(f"Battery inverter power  : {res.P_nom_bss:.2f} kW")
    print(f"EV capacity             : {res.C_ev:.2f} kWh")
    print(f"EV charger power        : {res.P_nom_ev:.2f} kW")
    print(f"Generator max power     : {res.P_max_gen:.2f} kW")
    print("=" * 60)

    return


# =============================================================================
# PLOTTING HELPERS
# =============================================================================

def add_ev_markers_matplotlib(ax, x, ev_soc_percent, t_arr, t_dep):
    # Mark EV arrival and departure points on the SOC plot.
    arr_idx = np.where(np.asarray(t_arr) == 1)[0]
    dep_idx = np.where(np.asarray(t_dep) == 1)[0]

    if len(arr_idx) > 0:
        ax.scatter(
            [x[i] for i in arr_idx],
            [ev_soc_percent[i] for i in arr_idx],
            marker="^",
            s=60,
            label="EV plugged in",
        )

    if len(dep_idx) > 0:
        ax.scatter(
            [x[i] for i in dep_idx],
            [ev_soc_percent[i] for i in dep_idx],
            marker="v",
            s=60,
            label="EV unplugged",
        )


# =============================================================================
# FULL-HORIZON JPG PLOT
# =============================================================================

def plot_res(res, plot_type="jpg"):
    from param import SOC_min_bss, SOC_max_bss, SOC_min_ev, SOC_max_ev, delta_T_max
    import matplotlib.pyplot as plt

    journal_style()

    x = res.datetime

    # Reconstruct total supply, demand and residual for checking.
    supply = (
        res.P_pv
        + res.P_gen
        + res.P_imp
        + np.maximum(-res.P_bss, 0.0)
        + np.maximum(-res.P_ev, 0.0)
    )

    demand = (
        res.P_load
        + res.P_hp_hot
        + res.P_hp_cold
        + np.maximum(res.P_bss, 0.0)
        + np.maximum(res.P_ev, 0.0)
        + res.P_exp
    )

    balance_residual = supply - demand

    # Convert stored energy to SOC percentage for plotting.
    bss_soc_percent = 100 * res.SOC_bss / res.C_bss
    ev_soc_percent = 100 * res.SOC_ev / res.C_ev

    bss_min_percent = 100 * SOC_min_bss
    bss_max_percent = 100 * SOC_max_bss
    ev_min_percent = 100 * SOC_min_ev
    ev_max_percent = 100 * SOC_max_ev

    T_upper = res.T_set + delta_T_max
    T_lower = res.T_set - delta_T_max

    fig, axs = plt.subplots(5, 1, figsize=(16, 20), sharex=True)
    fig.suptitle(
        "Residential Microgrid Optimisation Results",
        fontsize=16,
        fontweight="bold",
        y=0.96,
    )

    # Main power flows.
    axs[0].plot(x, res.P_load, label="Load")
    axs[0].plot(x, res.P_pv, label="PV")
    axs[0].plot(x, res.P_gen, label="Generator")
    axs[0].plot(x, res.P_imp, label="Grid Import")
    axs[0].plot(x, res.P_exp, label="Grid Export")
    axs[0].plot(x, res.P_hp_hot, label="HP Heating")
    axs[0].plot(x, res.P_hp_cold, label="HP Cooling")
    axs[0].set_ylabel("kW")
    axs[0].set_title("1) Main Power Flows [kW]", fontsize=12, fontweight="bold", loc="left")
    axs[0].axhline(0, color="black", linestyle=":", alpha=0.6)

    # Battery and EV charging/discharging power.
    axs[1].plot(x, res.P_bss, label="Battery Power")
    axs[1].plot(x, res.P_ev, label="EV Power")
    axs[1].set_ylabel("kW")
    axs[1].set_title(
        "2) Battery and EV Power [kW] (+ = charging, - = discharging)",
        fontsize=12,
        fontweight="bold",
        loc="left",
    )
    axs[1].axhline(0, color="black", linestyle=":", alpha=0.6)

    # Battery and EV SOC.
    axs[2].plot(x, bss_soc_percent, label="Battery SOC [%]")
    axs[2].plot(x, ev_soc_percent, label="EV SOC [%]")

    add_ev_markers_matplotlib(
        ax=axs[2],
        x=x,
        ev_soc_percent=ev_soc_percent,
        t_arr=res.t_arr,
        t_dep=res.t_dep,
    )

    axs[2].plot(x, np.full(len(x), bss_min_percent), label="Battery Min SOC", linestyle="--", color="C0", lw=0.7)
    axs[2].plot(x, np.full(len(x), bss_max_percent), label="Battery Max SOC", linestyle="--", color="C0", lw=0.7)
    axs[2].plot(x, np.full(len(x), ev_min_percent), label="EV Min SOC", linestyle=":", color="C1", lw=0.8)
    axs[2].plot(x, np.full(len(x), ev_max_percent), label="EV Max SOC", linestyle=":", color="C1", lw=0.8)
    axs[2].set_ylabel("SOC [%]")
    axs[2].set_title("3) Battery and EV State of Charge [%]", fontsize=12, fontweight="bold", loc="left")

    # Indoor temperature and comfort limits.
    axs[3].plot(x, res.T_hp, label="Indoor Temperature")
    axs[3].plot(x, res.T_set, label="Temperature Setpoint", linestyle="--", color="black", lw=0.9)
    axs[3].plot(x, T_upper, label="Comfort Upper Limit", linestyle=":", color="#777777", lw=0.8)
    axs[3].plot(x, T_lower, label="Comfort Lower Limit", linestyle=":", color="#777777", lw=0.8)
    axs[3].set_ylabel("°C")
    axs[3].set_title("4) Indoor Temperature [°C]", fontsize=12, fontweight="bold", loc="left")

    # Power balance verification.
    axs[4].plot(x, supply, label="Total Supply")
    axs[4].plot(x, demand, label="Total Demand", linestyle="--", color="C1")
    axs[4].plot(x, balance_residual, label="Balance residual", linestyle="-", color="C2", lw=0.8)
    axs[4].set_ylabel("kW")
    axs[4].set_xlabel("Time")
    axs[4].set_title("5) Power Balance Check [kW]", fontsize=12, fontweight="bold", loc="left")
    axs[4].axhline(0, color="black", linestyle=":", alpha=0.6)

    for ax in axs:
        ax.legend(loc="upper right", bbox_to_anchor=(1.15, 1.0))

    plt.tight_layout(rect=[0, 0, 0.88, 0.95])
    plt.savefig(os.path.join(FIG_DIR, "microgrid_results.jpg"), dpi=300, format="jpg")
    plt.close()

    return fig


# =============================================================================
# PRICE SENSITIVITY PLOT
# =============================================================================

def plot_price_sensitivity(res):
    """
    Post-processes the solved dispatch under different price scenarios.
    The optimisation is not repeated; only operating cost is recalculated.
    """

    import matplotlib.pyplot as plt

    journal_style()

    x = res.datetime
    n = len(x)

    progress = np.linspace(0, 1, n)
    hours = np.array([dt.hour + dt.minute / 60 for dt in x])

    # Import prices include time-of-use effects.
    evening_peak = np.where((hours >= 17) & (hours <= 21), 1.25, 1.00)
    night_discount = np.where((hours >= 0) & (hours <= 6), 0.85, 1.00)

    # Import and export prices are varied separately.
    price_cases = {
        "Base prices": {
            # Constant import and export prices.
            "PI_imp_t": PI_imp * np.ones(n),
            "PI_exp_t": PI_exp * np.ones(n),
        },
        "Base + TOU": {
            # Same base prices, but import price has evening peak and night discount.
            "PI_imp_t": PI_imp * evening_peak * night_discount,
            "PI_exp_t": PI_exp * np.ones(n),
        },
        "Import price rises": {
            # Import price rises gradually by 50%; export price stays constant.
            "PI_imp_t": PI_imp * (1.0 + 0.50 * progress) * evening_peak * night_discount,
            "PI_exp_t": PI_exp * np.ones(n),
        },
        "Export price falls": {
            # Import price stays constant; export price falls gradually by 50%.
            "PI_imp_t": PI_imp * np.ones(n),
            "PI_exp_t": PI_exp * (1.0 - 0.50 * progress),
        },
        "High import / low export": {
            # Import price rises strongly by 70%; export price stays at half its base value.
            "PI_imp_t": PI_imp * (1.0 + 0.70 * progress) * evening_peak * night_discount,
            "PI_exp_t": PI_exp * 0.50 * np.ones(n),
        },
    }

    summary = {}

    for case_name, prices in price_cases.items():
        PI_imp_t = prices["PI_imp_t"]
        PI_exp_t = prices["PI_exp_t"]

        # Recalculate operating cost using the same solved dispatch.
        import_cost = np.sum(delta_t * PI_imp_t * res.P_imp)
        export_revenue = np.sum(delta_t * PI_exp_t * res.P_exp)
        generator_cost = np.sum(delta_t * PI_gen * res.P_gen)

        net_opex = import_cost + generator_cost - export_revenue

        summary[case_name] = {
            "Import cost": import_cost,
            "Generator cost": generator_cost,
            "Export revenue": export_revenue,
            "Net OPEX": net_opex,
            "Average import price": np.mean(PI_imp_t),
            "Average export price": np.mean(PI_exp_t),
        }

    case_names = list(summary.keys())
    net_opex_values = [summary[c]["Net OPEX"] for c in case_names]
    import_cost_values = [summary[c]["Import cost"] for c in case_names]
    export_revenue_values = [summary[c]["Export revenue"] for c in case_names]

    fig, axs = plt.subplots(2, 1, figsize=(12, 10))

    fig.suptitle(
        "Post-processing Price Sensitivity for the Given System",
        fontsize=15,
        fontweight="bold",
        y=0.98,
    )

    # Net operating cost by price scenario.
    axs[0].bar(case_names, net_opex_values)
    axs[0].set_ylabel("Net operating cost (€)")
    axs[0].set_title(
        "A) Net OPEX under evolving price scenarios",
        fontsize=12,
        fontweight="bold",
        loc="left",
    )

    for i, value in enumerate(net_opex_values):
        axs[0].text(
            i,
            value,
            f"{value:.0f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    axs[0].tick_params(axis="x", rotation=15)

    # Compare import cost against export revenue.
    x_pos = np.arange(len(case_names))
    width = 0.35

    axs[1].bar(
        x_pos - width / 2,
        import_cost_values,
        width,
        label="Import cost",
    )

    axs[1].bar(
        x_pos + width / 2,
        export_revenue_values,
        width,
        label="Export revenue",
    )

    axs[1].set_xticks(x_pos)
    axs[1].set_xticklabels(case_names, rotation=15, ha="right")
    axs[1].set_ylabel("Cost / revenue (€)")
    axs[1].set_title(
        "B) Decorrelated import cost and export revenue",
        fontsize=12,
        fontweight="bold",
        loc="left",
    )
    axs[1].legend()

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.savefig(os.path.join(FIG_DIR, "price_sensitivity_combined.jpg"), dpi=300, format="jpg")
    plt.close()

    print("\n" + "=" * 60)
    print("PRICE SENSITIVITY ANALYSIS")
    print("=" * 60)

    for case_name, values in summary.items():
        print(
            f"{case_name:25s} | "
            f"Import cost = {values['Import cost']:.2f} € | "
            f"Export revenue = {values['Export revenue']:.2f} € | "
            f"Generator cost = {values['Generator cost']:.2f} € | "
            f"Net OPEX = {values['Net OPEX']:.2f} €"
        )

    print("=" * 60)
    print("Saved price sensitivity plot:")
    print("  price_sensitivity_combined.jpg")
    print("=" * 60)

    return summary


# =============================================================================
# REPRESENTATIVE DAY SELECTION
# =============================================================================

def score_day_vector(score, days, category):
    return np.array([score[d, category] for d in days])


def select_representative_days_optimised(res):
    import pyomo.environ as pyo

    steps_per_day = int(round(24 / delta_t))
    n_days = len(res.t) // steps_per_day
    days = range(n_days)

    categories = [
        "high_pv",
        "low_pv_heating",
        "ev_critical",
        "high_revenue",
        "low_revenue",
    ]

    daily_pv_available = np.zeros(n_days)
    daily_hp_heating = np.zeros(n_days)
    daily_ev_activity = np.zeros(n_days)
    daily_export = np.zeros(n_days)
    daily_grid_import = np.zeros(n_days)
    daily_import_cost = np.zeros(n_days)
    daily_export_revenue = np.zeros(n_days)
    daily_net_revenue = np.zeros(n_days)

    # Use export price from the solved case if available.
    export_price = getattr(res, "export_price_used", PI_exp)

    for d in days:
        i0 = d * steps_per_day
        i1 = (d + 1) * steps_per_day

        daily_pv_available[d] = np.sum(res.P_pv_max[i0:i1]) * delta_t
        daily_hp_heating[d] = np.sum(res.P_hp_hot[i0:i1]) * delta_t
        daily_ev_activity[d] = np.sum(np.abs(res.P_ev[i0:i1])) * delta_t
        daily_export[d] = np.sum(res.P_exp[i0:i1]) * delta_t
        daily_grid_import[d] = np.sum(res.P_imp[i0:i1]) * delta_t

        daily_import_cost[d] = PI_imp * daily_grid_import[d]
        daily_export_revenue[d] = export_price * daily_export[d]
        daily_net_revenue[d] = daily_export_revenue[d] - daily_import_cost[d]

        if hasattr(res, "t_dep"):
            daily_ev_activity[d] += 10.0 * np.sum(res.t_dep[i0:i1])  # favour days with a departure

    def norm(x):
        xmax = np.max(x)
        xmin = np.min(x)
        if abs(xmax - xmin) < 1e-9:
            return np.zeros_like(x)
        return (x - xmin) / (xmax - xmin)

    pv_norm = norm(daily_pv_available)
    hp_norm = norm(daily_hp_heating)
    ev_norm = norm(daily_ev_activity)
    exp_norm = norm(daily_export)
    imp_norm = norm(daily_grid_import)
    net_revenue_norm = norm(daily_net_revenue)
    low_revenue_norm = norm(-daily_net_revenue)

    score = {}

    for d in days:
        score[d, "high_pv"] = float(0.75 * pv_norm[d] + 0.25 * exp_norm[d])
        score[d, "low_pv_heating"] = float(
            0.55 * hp_norm[d] + 0.30 * imp_norm[d] + 0.15 * (1.0 - pv_norm[d])
        )
        score[d, "ev_critical"] = float(ev_norm[d])
        score[d, "high_revenue"] = float(0.70 * net_revenue_norm[d] + 0.30 * exp_norm[d])
        score[d, "low_revenue"] = float(0.70 * low_revenue_norm[d] + 0.30 * imp_norm[d])

    # Choose one distinct day per category.
    m = pyo.ConcreteModel()
    m.D = pyo.Set(initialize=list(days))
    m.C = pyo.Set(initialize=categories)
    m.x = pyo.Var(m.D, m.C, within=pyo.Binary)

    m.one_day_per_category = pyo.Constraint(
        m.C,
        rule=lambda m, c: sum(m.x[d, c] for d in m.D) == 1,
    )

    # each day used for one category at most
    m.no_duplicate_day = pyo.Constraint(
        m.D,
        rule=lambda m, d: sum(m.x[d, c] for c in m.C) <= 1,
    )

    m.objective = pyo.Objective(
        expr=sum(score[d, c] * m.x[d, c] for d in m.D for c in m.C),
        sense=pyo.maximize,
    )

    solver = pyo.SolverFactory("gurobi")
    if not solver.available(exception_flag=False):
        solver = pyo.SolverFactory("appsi_highs")

    solver.solve(m, tee=False)

    selected = {}

    for c in categories:
        for d in days:
            if pyo.value(m.x[d, c]) > 0.5:
                selected[c] = int(d)

    # Fallback: choose the highest-scoring day for each category.
    if len(selected) != len(categories):
        selected = {
            "high_pv": int(np.argmax(score_day_vector(score, days, "high_pv"))),
            "low_pv_heating": int(np.argmax(score_day_vector(score, days, "low_pv_heating"))),
            "ev_critical": int(np.argmax(score_day_vector(score, days, "ev_critical"))),
            "high_revenue": int(np.argmax(score_day_vector(score, days, "high_revenue"))),
            "low_revenue": int(np.argmax(score_day_vector(score, days, "low_revenue"))),
        }

    diagnostics = {}

    for c, d in selected.items():
        diagnostics[c] = {
            "day_index": d,
            "date": res.datetime[d * steps_per_day].date(),
            "pv_available_kwh": daily_pv_available[d],
            "hp_heating_kwh": daily_hp_heating[d],
            "ev_activity_score": daily_ev_activity[d],
            "grid_import_kwh": daily_grid_import[d],
            "grid_export_kwh": daily_export[d],
            "import_cost": daily_import_cost[d],
            "export_revenue": daily_export_revenue[d],
            "net_revenue": daily_net_revenue[d],
            "selection_score": score[d, c],
        }

    return selected, diagnostics


# =============================================================================
# REPRESENTATIVE DAY JPG PLOT
# =============================================================================

def plot_representative_day(res, day_index, title, filename, plot_type="jpg"):
    from param import SOC_min_bss, SOC_max_bss, SOC_min_ev, SOC_max_ev, delta_T_max
    import matplotlib.pyplot as plt

    journal_style()

    steps_per_day = int(round(24 / delta_t))
    i0 = day_index * steps_per_day
    i1 = (day_index + 1) * steps_per_day

    # Slice one representative day from the full-year results.
    x = res.datetime[i0:i1]

    P_load = res.P_load[i0:i1]
    P_pv = res.P_pv[i0:i1]
    P_gen = res.P_gen[i0:i1]
    P_imp = res.P_imp[i0:i1]
    P_exp = res.P_exp[i0:i1]
    P_hp_hot = res.P_hp_hot[i0:i1]
    P_hp_cold = res.P_hp_cold[i0:i1]
    P_bss = res.P_bss[i0:i1]
    P_ev = res.P_ev[i0:i1]
    SOC_bss = res.SOC_bss[i0:i1]
    SOC_ev = res.SOC_ev[i0:i1]
    t_arr = res.t_arr[i0:i1]
    t_dep = res.t_dep[i0:i1]
    T_hp = res.T_hp[i0:i1]
    T_set = res.T_set[i0:i1]

    supply = P_pv + P_gen + P_imp + np.maximum(-P_bss, 0.0) + np.maximum(-P_ev, 0.0)
    demand = P_load + P_hp_hot + P_hp_cold + np.maximum(P_bss, 0.0) + np.maximum(P_ev, 0.0) + P_exp
    residual = supply - demand

    bss_soc_percent = 100 * SOC_bss / res.C_bss
    ev_soc_percent = 100 * SOC_ev / res.C_ev

    T_upper = T_set + delta_T_max
    T_lower = T_set - delta_T_max

    fig, axs = plt.subplots(5, 1, figsize=(15, 16), sharex=True)
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.96)

    axs[0].plot(x, P_load, label="Load")
    axs[0].plot(x, P_pv, label="PV")
    axs[0].plot(x, P_gen, label="Generator")
    axs[0].plot(x, P_imp, label="Grid import")
    axs[0].plot(x, P_exp, label="Grid export")
    axs[0].plot(x, P_hp_hot, label="HP heating")
    axs[0].plot(x, P_hp_cold, label="HP cooling")
    axs[0].set_ylabel("kW")
    axs[0].set_title("Power dispatch [kW]", fontsize=11, fontweight="bold", loc="left")
    axs[0].axhline(0, color="black", linestyle=":", alpha=0.6)

    axs[1].plot(x, P_bss, label="Battery power")
    axs[1].plot(x, P_ev, label="EV power")
    axs[1].set_ylabel("kW")
    axs[1].set_title(
        "Battery and EV power [kW] (+ = charge, - = discharge)",
        fontsize=11,
        fontweight="bold",
        loc="left",
    )
    axs[1].axhline(0, color="black", linestyle=":", alpha=0.6)

    axs[2].plot(x, bss_soc_percent, label="Battery SOC")
    axs[2].plot(x, ev_soc_percent, label="EV SOC")

    add_ev_markers_matplotlib(
        ax=axs[2],
        x=x,
        ev_soc_percent=ev_soc_percent,
        t_arr=t_arr,
        t_dep=t_dep,
    )

    axs[2].plot(x, np.full(len(x), 100 * SOC_min_bss), label="Battery min", linestyle="--", color="C0", lw=0.7)
    axs[2].plot(x, np.full(len(x), 100 * SOC_max_bss), label="Battery max", linestyle="--", color="C0", lw=0.7)
    axs[2].plot(x, np.full(len(x), 100 * SOC_min_ev), label="EV min", linestyle=":", color="C1", lw=0.8)
    axs[2].plot(x, np.full(len(x), 100 * SOC_max_ev), label="EV max", linestyle=":", color="C1", lw=0.8)
    axs[2].set_ylabel("SOC [%]")
    axs[2].set_title("State of charge [%]", fontsize=11, fontweight="bold", loc="left")

    axs[3].plot(x, T_hp, label="Indoor temperature")
    axs[3].plot(x, T_set, label="Setpoint", linestyle="--", color="black", lw=0.9)
    axs[3].plot(x, T_upper, label="Comfort upper", linestyle=":", color="#777777", lw=0.8)
    axs[3].plot(x, T_lower, label="Comfort lower", linestyle=":", color="#777777", lw=0.8)
    axs[3].set_ylabel("°C")
    axs[3].set_title("Thermal comfort [°C]", fontsize=11, fontweight="bold", loc="left")

    axs[4].plot(x, supply, label="Total supply")
    axs[4].plot(x, demand, label="Total demand", linestyle="--", color="C1")
    axs[4].plot(x, residual, label="Residual", linestyle="-", color="C2", lw=0.8)
    axs[4].set_ylabel("kW")
    axs[4].set_xlabel("Time")
    axs[4].set_title("Power balance check [kW]", fontsize=11, fontweight="bold", loc="left")
    axs[4].axhline(0, color="black", linestyle=":", alpha=0.6)

    for ax in axs:
        ax.legend(loc="upper right", bbox_to_anchor=(1.15, 1.0))

    plt.tight_layout(rect=[0, 0, 0.88, 0.95])

    clean_filename = filename.replace(".html", "").replace(".jpg", "") + ".jpg"
    plt.savefig(os.path.join(FIG_DIR, os.path.basename(clean_filename)), dpi=300, format="jpg")
    plt.close()

    return fig


def plot_representative_days(res, plot_type="jpg"):
    selected, diagnostics = select_representative_days_optimised(res)

    print("\n" + "=" * 60)
    print("OPTIMISED REPRESENTATIVE DAYS SELECTED")
    print("=" * 60)

    for category, info in diagnostics.items():
        print(
            f"{category:20s} | "
            f"day {info['day_index']:3d} | "
            f"{info['date']} | "
            f"PV={info['pv_available_kwh']:.2f} kWh | "
            f"HP={info['hp_heating_kwh']:.2f} kWh | "
            f"EV score={info['ev_activity_score']:.2f} | "
            f"Imp={info['grid_import_kwh']:.2f} kWh | "
            f"Exp={info['grid_export_kwh']:.2f} kWh | "
            f"Net revenue={info['net_revenue']:.2f} | "
            f"score={info['selection_score']:.3f}"
        )

    figs = {}

    figs["high_pv"] = plot_representative_day(
        res,
        selected["high_pv"],
        title="Representative Day 1: Optimised High-PV Day",
        filename="representative_day_1_high_pv.jpg",
    )

    figs["low_pv_heating"] = plot_representative_day(
        res,
        selected["low_pv_heating"],
        title="Representative Day 2: Optimised Low-PV / Heating-Critical Day",
        filename="representative_day_2_low_pv_heating.jpg",
    )

    figs["ev_critical"] = plot_representative_day(
        res,
        selected["ev_critical"],
        title="Representative Day 3: Optimised EV-Critical Day",
        filename="representative_day_3_ev_critical.jpg",
    )

    figs["high_revenue"] = plot_representative_day(
        res,
        selected["high_revenue"],
        title="Representative Day 4: Optimised High-Revenue Day",
        filename="representative_day_4_high_revenue.jpg",
    )

    figs["low_revenue"] = plot_representative_day(
        res,
        selected["low_revenue"],
        title="Representative Day 5: Optimised Low-Revenue / Negative-Revenue Day",
        filename="representative_day_5_low_revenue.jpg",
    )

    print("=" * 60)
    print("Saved representative-day plots (JPG format):")
    print("  representative_day_1_high_pv.jpg")
    print("  representative_day_2_low_pv_heating.jpg")
    print("  representative_day_3_ev_critical.jpg")
    print("  representative_day_4_high_revenue.jpg")
    print("  representative_day_5_low_revenue.jpg")
    print("=" * 60)

    return figs


# =============================================================================
# SOLVER AND MODEL UPDATE
# =============================================================================

def solve_model(m, res):
    # HiGHS (open source) by default; set MICROGRID_SOLVER=gurobi to use Gurobi if installed.
    solver_name = os.environ.get("MICROGRID_SOLVER", "appsi_highs")
    solver = SolverFactory(solver_name)
    output = solver.solve(m)
    status = output.solver.status

    if status == SolverStatus.ok:
        print("Simulation completed")
        res = save_results(res, m)
        res.save_sizing_results(m)
        return m, res

    elif status == SolverStatus.warning:
        print("Solver finished with a warning.")

    elif status == SolverStatus.error:
        print("Solver encountered an error and did not converge.")

    elif status == SolverStatus.aborted:
        print("Solver was aborted before completing the optimisation.")

    else:
        print("Solver status unknown.")

    return None, None


def update_model(model, res, SOC_0_bss, SOC_0_ev, T_0_hp):
    # Update mutable model inputs for rolling-horizon or repeated simulations.
    for t in res.t:
        model.P_load[t] = res.P_load[t]
        model.P_pv_max[t] = res.P_pv_max[t]
        model.EV_connected[t] = res.EV_connected[t]
        model.t_arr[t] = res.t_arr[t]
        model.t_dep[t] = res.t_dep[t]
        model.SOC_i_ev[t] = res.SOC_i_ev[t]
        model.T_set[t] = res.T_set[t]
        model.P_loss[t] = res.P_loss[t]

    model.SOC_0_bss = SOC_0_bss
    model.SOC_0_ev = SOC_0_ev
    model.T_0_hp = T_0_hp

    return model


# =============================================================================
# RESULTS CLASS
# =============================================================================

class Results:
    def __init__(self, start_time, n_days, yearly_kwh, yearly_km):
        self.start_time = start_time
        self.t_s = int(n_days * 24 / delta_t)
        self.n_days = n_days
        self.yearly_kwh = yearly_kwh
        self.yearly_km = yearly_km
        self.t = np.arange(0, self.t_s)

        # Initialise result arrays before optimisation.
        self.P_pv = np.zeros(self.t_s)
        self.P_bss = np.zeros(self.t_s)
        self.P_ev = np.zeros(self.t_s)
        self.P_gen = np.zeros(self.t_s)
        self.P_imp = np.zeros(self.t_s)
        self.P_exp = np.zeros(self.t_s)
        self.P_hp_hot = np.zeros(self.t_s)
        self.P_hp_cold = np.zeros(self.t_s)
        self.T_hp = np.zeros(self.t_s)
        self.SOC_ev = np.zeros(self.t_s)
        self.SOC_bss = np.zeros(self.t_s)

        self.SOC_bss_i = 0.5

        csv_path = DATA_PATH

        self.df = pd.read_csv(
            csv_path,
            delimiter=";",
            index_col="DateTime",
            parse_dates=True,
            date_format="%Y-%m-%d %H:%M:%S",
        )

        # Build timestep timestamps using delta_t resolution.
        self.datetime = [
            self.start_time + timedelta(hours=float(t * delta_t))
            for t in self.t
        ]

        df_slice = self.df.loc[self.datetime]  # profiles must share delta_t resolution

        # Load input profiles from the CSV file.
        self.P_load = np.clip(df_slice["Load"].values, 0, None) * self.yearly_kwh  # normalised profile x annual kWh
        self.P_pv_max = np.clip(df_slice["PV"].values, 0, None)
        self.EV_connected = df_slice["EV"].values
        self.P_loss = df_slice["P_loss"].values
        self.T_set = df_slice["T_set"].values

        # EV arrival SOC is only set at the first connected timestep.
        self.SOC_i_ev = np.array([
            SOC_target_ev * C_ev - (self.EV_connected[t] * self.yearly_km) / (5e6)
            if self.EV_connected[t] > 0 and (t == 0 or self.EV_connected[t - 1] == 0)
            else 0
            for t in range(self.t_s)
        ])

        # Detect EV arrival and departure events.
        self.t_arr = np.array([
            1
            if self.EV_connected[t] > 0 and (t == 0 or self.EV_connected[t - 1] == 0)
            else 0
            for t in range(self.t_s)
        ])

        self.t_dep = np.array([
            1
            if self.EV_connected[t] == 0 and (t > 0 and self.EV_connected[t - 1] > 0)
            else 0
            for t in range(self.t_s)
        ])

        # If EV is still connected at the final timestep, force a final departure check.
        if self.EV_connected[-1] > 0:
            self.t_dep[-1] = 1

        # Convert EV profile to binary connection status.
        self.EV_connected = np.array([
            1 if val > 0 else 0
            for val in self.EV_connected
        ])

    def save_sizing_results(self, m):
        self.C_bss = value(m.C_bss)
        self.P_nom_bss = value(m.P_nom_bss)
        self.C_pv = value(m.C_pv)
        self.P_nom_pv = value(m.P_nom_pv)
        self.C_ev = value(m.C_ev)
        self.P_nom_ev = value(m.P_nom_ev)
        self.P_max_gen = value(m.P_max_gen)


# =============================================================================
# SAVE OPTIMISATION RESULTS
# =============================================================================

def save_results(res, m):
    # Save power-flow variables from the solved Pyomo model.
    res.P_imp = np.array([value(m.P_imp[t]) for t in m.periods])
    res.P_exp = np.array([value(m.P_exp[t]) for t in m.periods])
    res.P_pv = np.array([value(m.P_pv[t]) for t in m.periods])

    # Positive storage power means charging; negative means discharging.
    res.P_bss = np.array([
        value(m.P_charge_bss[t]) - value(m.P_discharge_bss[t])
        for t in m.periods
    ])

    res.P_ev = np.array([
        value(m.P_charge_ev[t]) - value(m.P_discharge_ev[t])
        for t in m.periods
    ])

    res.P_gen = np.array([value(m.P_gen[t]) for t in m.periods])
    res.P_hp_hot = np.array([value(m.P_hp_hot[t]) for t in m.periods])
    res.P_hp_cold = np.array([value(m.P_hp_cold[t]) for t in m.periods])
    res.T_hp = np.array([value(m.T_hp[t]) for t in m.periods])
    res.SOC_ev = np.array([value(m.SOC_ev[t]) for t in m.periods])
    res.SOC_bss = np.array([value(m.SOC_bss[t]) for t in m.periods])

    total_hours = len(list(m.periods)) * delta_t
    scaling_factor = total_hours / (8760 * inv_hor)  # CAPEX share of the simulated period

    # CAPEX is calculated from the final selected or fixed asset sizes.
    res.capex_total = (
        PI_c_pv * value(m.C_pv)
        + PI_c_bss * value(m.C_bss)
        + PI_c_inv * (value(m.P_nom_pv) + value(m.P_nom_bss))
        + PI_c_gen * value(m.P_max_gen)
    )

    res.capex_scaled = scaling_factor * res.capex_total

    # Use case-specific export price if provided; otherwise use the default.
    export_price = getattr(m, "export_price_used", PI_exp)
    res.export_price_used = export_price

    res.opex_period = sum(
        delta_t * (
            PI_imp * value(m.P_imp[t])
            + PI_gen * value(m.P_gen[t])
            - export_price * value(m.P_exp[t])
        )
        for t in m.periods
    )

    res.opex_year = res.opex_period * (8760 / total_hours)  # linear extrapolation to a year
    res.total_cost = res.capex_scaled + res.opex_period
    res.objective = value(m.objective)

    return res