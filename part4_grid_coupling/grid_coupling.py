"""
Part 4: coupling the Pyomo dispatch optimisation with a pandapower grid model.

Thirteen homes with rooftop PV (and batteries in half of them) sit on a rural low-voltage
feeder (Kerber "Landnetz Freileitung 1", 160 kVA transformer, overhead lines). The workflow:

  1. Optimise the homes' dispatch in Pyomo (LP, minimum import cost minus export revenue).
  2. Map each home's net grid exchange onto its bus and run an AC load flow for every 15 min.
  3. Where voltage or loading limits are violated, tighten the export (or import) limit of the
     homes that cause it in those time steps, and re-optimise.
  4. Repeat until the schedule is network-feasible.

The same loop is then used to compare candidate connection points for a larger PV plant,
together with the short-circuit power at each point and a grid-reinforcement alternative.

Usage:  python grid_coupling.py [start_date] [n_days]      (default 2021-06-20, 2 days)
"""
import os
import sys
import time

import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pandapower as pp
import pandapower.networks as pn
import pandapower.shortcircuit as sc
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIG = os.path.join(HERE, "figures")
RES = os.path.join(ROOT, "results")
SOLVER = os.environ.get("MICROGRID_SOLVER", "appsi_highs")

# ---------------- parameters ----------------
DT = 0.25                     # h
PI_IMP, PI_EXP = 0.40, 0.03   # EUR/kWh, as in part 2
V_MAX, V_MIN = 1.05, 0.95     # planning limits (p.u.)
LOAD_MAX = 100.0              # line and transformer loading limit (%)
TIGHTEN = 0.85                # export/import limit set to 85% of the violating flow
MAX_IT = 30
PLANT_KWP = 60.0              # larger PV plant for the siting study
RNG = np.random.default_rng(7)

plt.rcParams.update({'font.size': 8, 'axes.labelsize': 9, 'legend.fontsize': 7,
                     'axes.spines.top': False, 'axes.spines.right': False, 'axes.linewidth': 0.8,
                     'xtick.direction': 'out', 'ytick.direction': 'out', 'axes.grid': False,
                     'savefig.dpi': 300, 'savefig.bbox': 'tight'})
OI = ['#000000', '#E69F00', '#0072B2', '#009E73', '#D55E00', '#56B4E9', '#CC79A7']


def panel(ax, letter):
    ax.text(-0.13, 1.04, letter, transform=ax.transAxes, fontsize=11, fontweight='bold', va='bottom')


# ---------------- data ----------------
def build_homes(start, n_days):
    df = pd.read_csv(os.path.join(ROOT, "data", "annual", "annual_profiles_2021.csv"),
                     sep=";", parse_dates=["DateTime"], index_col="DateTime")
    i0 = df.index.get_loc(pd.Timestamp(start))
    T = 96 * n_days
    homes = []
    for k in range(13):
        shift = int(RNG.integers(-6, 7))                      # +/- 1.5 h, so homes are not identical
        load = np.roll(df["Load"].values, shift)[i0:i0 + T].clip(0) * RNG.uniform(3000, 5500)
        homes.append(dict(name=f"home_{k + 1}", bus=k + 2, load=load,
                          kwp=RNG.uniform(6, 12), bat=(k % 2 == 0)))
    pv_pu = df["PV"].values[i0:i0 + T].clip(0)
    return homes, pv_pu, df.index[i0:i0 + T]


def add_plant(homes, bus):
    T = len(homes[0]["load"])
    return homes + [dict(name="pv_plant", bus=bus, load=np.zeros(T), kwp=PLANT_KWP, bat=False)]


# ---------------- optimisation ----------------
def optimise(homes, pv_pu, e_max, i_max):
    H, T = range(len(homes)), range(len(pv_pu))
    m = pyo.ConcreteModel()
    nn = pyo.NonNegativeReals
    m.imp, m.exp, m.curt = (pyo.Var(H, T, within=nn) for _ in range(3))
    m.ch, m.dis, m.soc = (pyo.Var(H, T, within=nn) for _ in range(3))
    cap = {h: (10.0 if homes[h]["bat"] else 0.0) for h in H}      # kWh
    pmax = {h: (5.0 if homes[h]["bat"] else 0.0) for h in H}       # kW
    pv = {(h, t): homes[h]["kwp"] * pv_pu[t] for h in H for t in T}
    m.c = pyo.ConstraintList()
    c = m.c
    for h in H:
        for t in T:
            c.add(pv[h, t] - m.curt[h, t] + m.imp[h, t] + m.dis[h, t]
                  == homes[h]["load"][t] + m.exp[h, t] + m.ch[h, t])
            c.add(m.curt[h, t] <= pv[h, t])
            c.add(m.exp[h, t] <= e_max[h, t])
            c.add(m.imp[h, t] <= i_max[h, t])
            c.add(m.ch[h, t] <= pmax[h])
            c.add(m.dis[h, t] <= pmax[h])
            prev = 0.5 * cap[h] if t == 0 else m.soc[h, t - 1]
            c.add(m.soc[h, t] == prev + (0.95 * m.ch[h, t] - m.dis[h, t] / 0.95) * DT)
            c.add(m.soc[h, t] >= 0.1 * cap[h])
            c.add(m.soc[h, t] <= 0.95 * cap[h])
        c.add(m.soc[h, T[-1]] >= 0.5 * cap[h])  # no free energy from the initial SOC
    m.obj = pyo.Objective(expr=sum((PI_IMP * m.imp[h, t] - PI_EXP * m.exp[h, t]
                                    # tiny cost: no simultaneous charge and discharge
                                    + 1e-4 * (m.ch[h, t] + m.dis[h, t])) * DT for h in H for t in T))
    pyo.SolverFactory(SOLVER).solve(m)
    g = lambda v: np.array([[pyo.value(v[h, t]) for t in T] for h in H])
    imp, exp, curt = g(m.imp), g(m.exp), g(m.curt)
    cost = float(((PI_IMP * imp - PI_EXP * exp) * DT).sum())
    return imp, exp, curt, cost


# ---------------- grid ----------------
def make_net(reinforced=False):
    net = pn.create_kerber_landnetz_freileitung_1()
    net.load.drop(net.load.index, inplace=True)
    net.ext_grid["s_sc_max_mva"], net.ext_grid["rx_max"] = 100.0, 0.1   # assumed MV grid strength
    if reinforced:
        for l in net.line.index:
            pp.change_std_type(net, l, "NAYY 4x150 SE", element="line")
        net.trafo["sn_mva"] = 0.4
    return net


def load_flow(net, homes, net_kw):
    """Run one AC load flow per time step. net_kw: homes x T, positive = import from grid."""
    if len(net.load) != len(homes):
        net.load.drop(net.load.index, inplace=True)
        for h in homes:
            pp.create_load(net, h["bus"], p_mw=0.0, name=h["name"])
    T = net_kw.shape[1]
    vm = np.zeros((len(net.bus), T)); ll = np.zeros((len(net.line), T)); tl = np.zeros(T)
    for t in range(T):
        net.load["p_mw"] = net_kw[:, t] / 1000.0
        pp.runpp(net, init="results" if t else "auto", numba=False)  # warm start from the previous step
        vm[:, t] = net.res_bus.vm_pu.values
        ll[:, t] = net.res_line.loading_percent.values
        tl[t] = net.res_trafo.loading_percent.values[0]
    return vm, ll, tl


def downstream(net, line):
    """Buses fed through a line on this radial feeder (buses with a higher index)."""
    return set(range(int(net.line.to_bus[line]), len(net.bus)))


def couple(homes, pv_pu, net, label):
    """Iterate optimisation and load flow until the schedule respects the grid limits."""
    H, T = len(homes), len(pv_pu)
    e_max = np.full((H, T), 1e3); i_max = np.full((H, T), 1e3)
    bus_of = np.array([h["bus"] for h in homes])
    hist = []
    for it in range(MAX_IT + 1):
        imp, exp, curt, cost = optimise(homes, pv_pu, e_max, i_max)
        vm, ll, tl = load_flow(net, homes, imp - exp)
        over_v, under_v = vm > V_MAX, vm < V_MIN
        over_l, over_t = ll > LOAD_MAX, tl > LOAD_MAX
        bad = over_v.any(0) | under_v.any(0) | over_l.any(0) | over_t
        hist.append(dict(case=label, iteration=it, violating_steps=int(bad.sum()),
                         max_voltage_pu=vm[1:].max(), min_voltage_pu=vm[1:].min(),
                         max_line_loading_pct=ll.max(), max_trafo_loading_pct=tl.max(),
                         curtailed_kwh=curt.sum() * DT, cost_eur=cost))
        if it == 0:
            vm0 = vm.copy()
        if not bad.any():
            break
        for t in np.where(bad)[0]:
            exporting = exp[:, t].sum() > imp[:, t].sum()  # decides which limit to tighten
            culprits = np.zeros(H, bool)
            culprits |= np.isin(bus_of, np.where(over_v[:, t] | under_v[:, t])[0])
            for l in np.where(over_l[:, t])[0]:
                culprits |= np.isin(bus_of, list(downstream(net, l)))
            if over_t[t]:
                culprits |= True
            if not culprits.any():
                culprits |= True  # no bus identified: tighten all
            if exporting:
                e_max[culprits, t] = TIGHTEN * exp[culprits, t]
            else:
                # never below the home's own load: only grid-charging of batteries can be shifted
                load_t = np.array([homes[h]["load"][t] for h in range(H)])
                i_max[culprits, t] = np.maximum(TIGHTEN * imp[culprits, t], load_t[culprits])
    return pd.DataFrame(hist), dict(vm0=vm0, vm=vm, ll=ll, tl=tl, imp=imp, exp=exp, curt=curt)


def short_circuit_mva(net, buses):
    sc.calc_sc(net, case="max", ip=False)
    # S_k = sqrt(3) U I_k''
    return {b: float(net.res_bus_sc.ikss_ka[b] * np.sqrt(3) * net.bus.vn_kv[b]) for b in buses}


# ---------------- main ----------------
def main(start="2021-06-20", n_days=2):
    t0 = time.time()
    homes, pv_pu, idx = build_homes(start, n_days)
    print(f"{len(homes)} homes, {sum(h['kwp'] for h in homes):.0f} kWp rooftop PV, "
          f"{sum(h['bat'] for h in homes)} batteries, {n_days} days from {start}")

    # A) homes only: does the optimised schedule respect the feeder?
    hist_base, res_base = couple(homes, pv_pu, make_net(), "Homes only")

    # B) siting of a larger PV plant at three candidate connection points
    candidates = {2: "Next to transformer", 8: "Mid-feeder", 14: "Feeder end"}
    sk = short_circuit_mva(make_net(), list(candidates))
    rows, hists = [], [hist_base]
    for bus, name in candidates.items():
        hs = add_plant(homes, bus)
        hist, res = couple(hs, pv_pu, make_net(), name)
        hists.append(hist)
        plant_pv = PLANT_KWP * pv_pu.sum() * DT
        # reinforcement alternative: unconstrained schedule on a reinforced feeder
        imp0, exp0, _, _ = optimise(hs, pv_pu, np.full((len(hs), len(pv_pu)), 1e3), np.full((len(hs), len(pv_pu)), 1e3))
        vm_r, ll_r, tl_r = load_flow(make_net(reinforced=True), hs, imp0 - exp0)
        rows.append(dict(connection_point=name, bus=bus, short_circuit_mva=sk[bus],
                         max_voltage_before_pu=hist.max_voltage_pu.iloc[0],
                         violating_steps_before=int(hist.violating_steps.iloc[0]),
                         iterations=int(hist.iteration.iloc[-1]),
                         plant_curtailment_pct=100 * res["curt"][-1].sum() * DT / plant_pv,
                         total_curtailed_kwh=res["curt"].sum() * DT,
                         total_curtailment_pct=100 * res["curt"].sum() / (sum(h["kwp"] for h in hs) * pv_pu.sum()),
                         extra_cost_eur=hist.cost_eur.iloc[-1] - hist.cost_eur.iloc[0],
                         reinforced_max_voltage_pu=vm_r[1:].max(),
                         reinforced_violating_steps=int(((vm_r > V_MAX).any(0) | (ll_r > LOAD_MAX).any(0)
                                                        | (tl_r > LOAD_MAX)).sum())))
        if bus == 14:
            res_end = res
    siting = pd.DataFrame(rows)
    history = pd.concat(hists, ignore_index=True)
    os.makedirs(RES, exist_ok=True)
    history.to_csv(os.path.join(RES, "part4_coupling_iterations.csv"), index=False)
    siting.to_csv(os.path.join(RES, "part4_siting.csv"), index=False)
    pd.set_option("display.width", 200)
    print(history.groupby("case").agg(iterations=("iteration", "max"),
                                      steps_before=("violating_steps", "first"),
                                      vmax_before=("max_voltage_pu", "first"),
                                      vmax_after=("max_voltage_pu", "last"),
                                      curtailed_kwh=("curtailed_kwh", "last")).round(3))
    print(siting.round(3).to_string(index=False))

    # ---------------- figures ----------------
    fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.9))
    hrs = np.arange(len(pv_pu)) * DT
    end_bus = len(res_end["vm"]) - 1
    ax[0].plot(hrs, res_end["vm0"][end_bus], color=OI[4], ls="--", lw=1, label="Before coupling")
    ax[0].plot(hrs, res_end["vm"][end_bus], color=OI[2], lw=1, label="After coupling")
    ax[0].axhline(V_MAX, color=OI[0], ls=":", lw=0.8, label="Limit 1.05 p.u.")
    ax[0].set(xlabel="Time (h)", ylabel="Voltage at feeder end (p.u.)")
    ax[0].legend(frameon=False, loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=3)
    panel(ax[0], "A")
    mk = ["o", "s", "^", "D"]
    for k, (case, g) in enumerate(history.groupby("case", sort=False)):
        ax[1].plot(g.iteration, g.max_voltage_pu, marker=mk[k], ms=3, lw=1, color=OI[k + 1 if k else 0], label=case)
    ax[1].axhline(V_MAX, color=OI[0], ls=":", lw=0.8)
    ax[1].set(xlabel="Iteration (-)", ylabel="Highest feeder voltage (p.u.)")
    ax[1].legend(frameon=False, loc="upper right")
    panel(ax[1], "B")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig1_coupling_voltage.png"))
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(4.0, 2.8))
    x = np.arange(len(siting))
    ax.bar(x, siting.total_curtailment_pct, color=OI[2], width=0.55)
    for i, r in siting.iterrows():
        ax.text(i, r.total_curtailment_pct + 0.15, f"S$_k$ = {r.short_circuit_mva:.1f} MVA", ha="center", fontsize=7)
    ax.set_xticks(x, siting.connection_point)
    ax.set(ylabel="PV energy curtailed, all units (%)", xlabel="Connection point of the 60 kWp plant")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig2_siting_curtailment.png"))
    plt.close(fig)
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main(*(sys.argv[1:2] or ["2021-06-20"]), int(sys.argv[2]) if len(sys.argv) > 2 else 2)
