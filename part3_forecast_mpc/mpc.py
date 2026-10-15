"""
Forecast-driven day-ahead MPC for the residential microgrid.

Two layers, as in a typical home energy management system:
    1. Day-ahead: each day at midnight the planning MILP from part 2
       (planning.create_model) is solved over the next 36 h (24 h committed
       plus a 12 h look-ahead) using forecasts of household load and PV.
    2. Real time: every 15 minutes the heat pump and generator follow the
       plan, the EV follows its planned power (charging more if its measured
       SOC falls behind the planned trajectory), the battery corrects the
       forecast error around its planned set-point (within power and SOC
       limits), and any PV surplus left over charges the EV further when it is
       plugged in. Whatever remains is imported or exported.
The measured end-of-day battery, EV and indoor-temperature states initialise
the next day's plan.

Strategies compared on the same days, with identical initial states:
    perfect     : plan with the measured load and PV (perfect-forecast benchmark)
    xgboost     : plan with the XGBoost day-ahead forecasts from forecasting.py
    persistence : plan with yesterday's profiles
    profile7    : plan with the 7-day same-quarter-hour average

Heat losses, temperature set-points and EV plug-in times are treated as known.

Usage:
    python forecasting.py                 # creates the forecasts first
    python mpc.py                         # default: two 14-day windows (Oct and Nov 2021)
    python mpc.py 2021-10-04 7            # custom start date and number of days
Outputs:
    ../results/part3_mpc_summary.csv
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import pyomo.environ as pyo

HERE = os.path.dirname(os.path.abspath(__file__))
PART2 = os.path.join(HERE, "..", "part2_planning_sizing")
sys.path.insert(0, PART2)

import planning                                    # noqa: E402  (part 2 model)
import utils as p2utils                            # noqa: E402
from param import (delta_t, C_ev, PI_imp, PI_exp, PI_gen,  # noqa: E402
                   SOC_min_bss, SOC_max_bss, eff_bss, SOC_min_ev, SOC_max_ev, eff_ev, P_nom_ev)

RESULTS_DIR = os.path.join(HERE, "..", "results")
STEPS = 96          # committed part of each plan (one day)
LOOKAHEAD = 48      # extra quarter-hours planned but not committed (12 h)

# Fixed system, identical to the part 2 planning base case.
SIZES = dict(C_pv=10, C_bss=40, P_nom_bss=10, P_nom_pv=10, P_max_gen=10)
STRATEGIES = {"perfect": None, "xgboost": "xgb", "persistence": "persist",
              "profile7": "profile7"}


class HorizonData:
    """Planning horizon in the format expected by planning.create_model.

    Index 0 holds the state at midnight (no power flows); indices 1..H are the
    quarter-hours being planned: the day itself plus a 12 h look-ahead, so
    that an early-morning EV departure is visible from the evening before.
    """

    def __init__(self, full, i0, load, pv_pu):
        H = len(load)
        n = H + 1
        sl = slice(i0, i0 + H)
        self.t_s = n

        def pad(x, first=0.0):
            return np.concatenate([[first], np.asarray(x, dtype=float)])

        self.P_load = pad(load)
        self.P_pv_max = pad(pv_pu)
        # index 0: plug state just before midnight
        self.EV_connected = pad(full.EV_connected[sl], full.EV_connected[i0 - 1] if i0 else 0)
        self.t_arr = pad(full.t_arr[sl])
        self.t_dep = pad(full.t_dep[sl])
        self.SOC_i_ev = pad(full.SOC_i_ev[sl])
        self.T_set = pad(full.T_set[sl], full.T_set[i0])
        self.P_loss = pad(full.P_loss[sl])


def solve_day(full, i0, load, pv_pu, state, solver):
    h = HorizonData(full, i0, load, pv_pu)
    m = planning.create_model(h, **SIZES)

    # Initial states from the previous day.
    m.SOC_0_bss = state["soc_bss"]
    m.SOC_0_ev = state["soc_ev"]
    m.T_0_hp = state["t_hp"]

    # No power exchange at index 0 (it only carries the midnight state).
    for v in (m.P_charge_bss, m.P_discharge_bss, m.P_charge_ev, m.P_discharge_ev,
              m.P_hp_hot, m.P_hp_cold, m.P_gen, m.P_imp, m.P_exp, m.P_pv):
        v[0].fix(0.0)

    # Same terminal rule for every strategy: end the day with at least the
    # battery energy it started with, so no plan "spends" the battery for free.
    # Applied at the end of the committed day, not of the look-ahead.
    m.terminal_bss = pyo.Constraint(expr=m.SOC_bss[STEPS] >= state["soc_bss"])

    res = solver.solve(m)
    if res.solver.termination_condition != pyo.TerminationCondition.optimal:
        raise RuntimeError(f"Day starting at index {i0}: {res.solver.termination_condition}")

    t = range(1, STEPS + 1)
    val = lambda var: np.array([pyo.value(var[k]) for k in t])
    plan = {
        "bss": val(m.P_charge_bss) - val(m.P_discharge_bss),
        "ev": val(m.P_charge_ev) - val(m.P_discharge_ev),
        "hp": val(m.P_hp_hot) + val(m.P_hp_cold),
        "gen": val(m.P_gen),
        "soc_ev": val(m.SOC_ev),
    }
    end = {"soc_bss": pyo.value(m.SOC_bss[STEPS]), "soc_ev": pyo.value(m.SOC_ev[STEPS]),
           "t_hp": pyo.value(m.T_hp[STEPS])}
    return plan, end


def realise(plan, load_act, pv_act_pu, load_fc, pv_fc_pu, soc_bss0, soc_ev0, ev_conn, ev_events):
    """Real-time layer applied to the measured load and PV.

    Battery set-point: P_plan - (net_measured - net_forecast), clipped to its
    limits. Remaining PV surplus raises EV charging (never lowers it).
    """
    C, Pmax = SIZES["C_bss"], SIZES["P_nom_bss"]
    cap = lambda pu: np.minimum(np.asarray(pu) * SIZES["C_pv"], SIZES["P_nom_pv"])
    pv_act, pv_fc = cap(pv_act_pu), cap(pv_fc_pu)
    t_arr, soc_arr = ev_events

    soc, soc_ev = soc_bss0, soc_ev0
    cost = imp_tot = exp_tot = 0.0
    for k in range(STEPS):
        if t_arr[k]:
            soc_ev = soc_arr[k]           # EV returns with its arrival SOC

        # EV: follow the plan, and catch up if the measured SOC is below the
        # planned trajectory, so departure targets are still met.
        p_ev = 0.0
        if ev_conn[k]:
            gap = plan["soc_ev"][k] - soc_ev
            # power to close the SOC gap in one step
            need = gap / (eff_ev * delta_t) if gap > 0 else gap * eff_ev / delta_t
            ev_ch = max(0.0, min(P_nom_ev, (SOC_max_ev * C_ev - soc_ev) / (eff_ev * delta_t)))
            ev_dis = max(0.0, min(P_nom_ev, (soc_ev - SOC_min_ev * C_ev) * eff_ev / delta_t))
            p_ev = float(np.clip(max(plan["ev"][k], need), -ev_dis, ev_ch))

        base = plan["hp"][k] - plan["gen"][k] + p_ev
        net_act = load_act[k] + base - pv_act[k]
        net_fc = load_fc[k] + base - pv_fc[k]

        ch_max = max(0.0, min(Pmax, (SOC_max_bss * C - soc) / (eff_bss * delta_t)))
        dis_max = max(0.0, min(Pmax, (soc - SOC_min_bss * C) * eff_bss / delta_t))
        # battery absorbs the forecast error
        p_bss = float(np.clip(plan["bss"][k] - (net_act - net_fc), -dis_max, ch_max))
        soc += delta_t * (eff_bss * max(p_bss, 0.0) - max(-p_bss, 0.0) / eff_bss)

        # Remaining PV surplus goes into the EV when it is plugged in.
        grid = net_act + p_bss
        if grid < 0 and ev_conn[k]:
            ev_room = max(0.0, min(P_nom_ev, (SOC_max_ev * C_ev - soc_ev) / (eff_ev * delta_t)) - max(p_ev, 0.0))
            extra = min(-grid, ev_room)
            p_ev += extra
            grid += extra
        soc_ev += delta_t * (eff_ev * max(p_ev, 0.0) - max(-p_ev, 0.0) / eff_ev)

        imp, exp = max(grid, 0.0), max(-grid, 0.0)
        cost += delta_t * (PI_imp * imp + PI_gen * plan["gen"][k] - PI_exp * exp)
        imp_tot += imp * delta_t
        exp_tot += exp * delta_t
    return cost, imp_tot, exp_tot, soc, soc_ev


def run_window(start, n_days, fc, full, index, solver):
    days = pd.date_range(start, periods=n_days, freq="D")
    summary = []
    for name, col in STRATEGIES.items():
        state = {"soc_bss": 0.5 * SIZES["C_bss"], "soc_ev": 0.5 * C_ev,
                 "t_hp": float(full.T_set[index.get_loc(days[0])])}
        cost = imp = exp = 0.0
        for day in days:
            i0 = index.get_loc(day)
            sl = slice(i0, i0 + STEPS)
            load_act, pv_act = full.P_load[sl], full.P_pv_max[sl]
            H = min(STEPS + LOOKAHEAD, len(index) - i0)  # shorter at the end of the year
            if col is None:
                load_f, pv_f = load_act, pv_act
                load_h = full.P_load[i0:i0 + H]
                pv_h = full.P_pv_max[i0:i0 + H]
            else:
                f = fc.loc[day: day + pd.Timedelta(minutes=15 * (STEPS - 1))]
                load_f, pv_f = f[f"load_{col}"].values, f[f"pv_{col}"].values
                # Only a day-ahead forecast exists at midnight, so the look-ahead
                # reuses the forecast for the first hours of the same day.
                load_h = np.concatenate([load_f, load_f[:H - STEPS]])
                pv_h = np.concatenate([pv_f, pv_f[:H - STEPS]])
            plan, planned_end = solve_day(full, i0, load_h, pv_h, state, solver)
            c, i, e, soc_end, soc_ev_end = realise(
                plan, load_act, pv_act, load_f, pv_f, state["soc_bss"], state["soc_ev"],
                full.EV_connected[sl], (full.t_arr[sl], full.SOC_i_ev[sl]))
            # Keep measured states strictly inside the model bounds (float drift).
            eps = 1e-6
            soc_end = float(np.clip(soc_end, SOC_min_bss * SIZES["C_bss"] + eps,
                                    SOC_max_bss * SIZES["C_bss"] - eps))
            soc_ev_end = float(np.clip(soc_ev_end, SOC_min_ev * C_ev + eps, SOC_max_ev * C_ev - eps))
            state = {**planned_end, "soc_bss": soc_end, "soc_ev": soc_ev_end}
            cost, imp, exp = cost + c, imp + i, exp + e
        summary.append({"window": f"{days[0]:%d %b} - {days[-1]:%d %b}", "strategy": name,
                        "days": n_days, "realised_cost_EUR": cost,
                        "grid_import_kWh": imp, "grid_export_kWh": exp})
        print(f"{name:12s} realised cost {cost:8.2f} EUR | import {imp:7.1f} kWh | export {exp:6.1f} kWh")

    out = pd.DataFrame(summary)
    base = out.loc[out.strategy == "perfect", "realised_cost_EUR"].iloc[0]
    out["extra_cost_vs_perfect_%"] = 100 * (out["realised_cost_EUR"] - base) / base
    return out


def plot_summary(out):
    """Extra realised cost over the perfect-forecast plan, per window."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from forecasting import OKABE, _journal_style, FIG_DIR

    _journal_style()
    strategies = [("xgboost", "XGBoost", OKABE["blue"]),
                  ("persistence", "Persistence", OKABE["orange"]),
                  ("profile7", "7-day profile", OKABE["green"])]
    windows = list(dict.fromkeys(out["window"]))
    fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
    w = 0.25
    for j, (key, label, color) in enumerate(strategies):
        vals = [out.loc[(out.window == win) & (out.strategy == key),
                        "extra_cost_vs_perfect_%"].iloc[0] for win in windows]
        ax.bar(np.arange(len(windows)) + (j - 1) * w, vals, w, color=color, label=label,
               edgecolor="black", linewidth=0.5)
    ax.set_xticks(np.arange(len(windows)), windows)
    ax.set_ylabel("Extra cost vs perfect forecast (%)")
    ax.set_xlabel("Test window (2021)")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3, handlelength=1.2,
              columnspacing=0.8)
    os.makedirs(FIG_DIR, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(FIG_DIR, f"mpc_extra_cost.{ext}"), dpi=300)
    plt.close(fig)


def main(windows):
    fc = pd.read_csv(os.path.join(RESULTS_DIR, "part3_forecasts_test.csv"),
                     index_col=0, parse_dates=True)
    full = p2utils.Results(datetime(2021, 1, 1), 365, yearly_kwh=10_000, yearly_km=20_000)
    index = pd.DatetimeIndex(full.datetime)
    solver = pyo.SolverFactory(os.environ.get("MICROGRID_SOLVER", "appsi_highs"))

    out = pd.concat([run_window(s, n, fc, full, index, solver) for s, n in windows],
                    ignore_index=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out.to_csv(os.path.join(RESULTS_DIR, "part3_mpc_summary.csv"), index=False, float_format="%.2f")
    print("\n" + out.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    if out["window"].nunique() > 1:
        plot_summary(out)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args:
        main([(args[0], int(args[1]) if len(args) > 1 else 14)])
    else:
        # Two test windows from the forecast test period (about 40 s in total).
        main([("2021-10-04", 14), ("2021-11-08", 14)])
