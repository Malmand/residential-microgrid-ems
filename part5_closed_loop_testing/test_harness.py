"""
Part 5: automated closed-loop testing of a battery site controller (software-in-the-loop).

Part 1 runs a controller in closed loop with a plant model. Part 5 turns that idea into a test
bench for a grid-connected battery storage site, the kind of setup that Hardware-in-the-Loop
testing automates on a real-time simulator:

  plant model      battery with power limits, efficiency, SOC limits, a first-order inverter
                   response and a ramp limit; a co-located wind farm; a grid connection limit
  device under     a site controller providing frequency containment reserve (FCR), following a
  test             trading schedule, protecting SOC and respecting the connection limit
  scenarios        grid events and site conditions (frequency steps, ramps and noise, low SOC,
                   wind surges, communication delay, schedule steps)
  checks           pass/fail criteria per scenario, written to a CSV and a Markdown report

The plant, controller and scenario are separate objects that only exchange measurements and
set-points, so the Python plant could be replaced by a real-time simulator without touching
the scenarios or checks. A first controller version (v1) is revised after the tests expose two weaknesses (v2), and
faults injected into v2 show that the tests catch typical bugs.

Usage:  python test_harness.py                     (160 simulation runs, about 20 s)
"""
import os
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(os.path.dirname(HERE), "results")
FIG = os.path.join(HERE, "figures")
DT = 0.2                      # s
F0 = 50.0                     # Hz

plt.rcParams.update({'font.size': 8, 'axes.labelsize': 9, 'legend.fontsize': 7,
                     'axes.spines.top': False, 'axes.spines.right': False, 'axes.linewidth': 0.8,
                     'xtick.direction': 'out', 'ytick.direction': 'out', 'axes.grid': False,
                     'savefig.dpi': 300, 'savefig.bbox': 'tight'})
OI = {'black': '#000000', 'orange': '#E69F00', 'blue': '#0072B2', 'green': '#009E73',
      'vermillion': '#D55E00', 'sky': '#56B4E9'}


# ---------------------------------------------------------------- site and plant
@dataclass
class Site:
    name: str
    p_nom: float          # MW
    e_nom: float          # MWh
    fcr: float            # MW of FCR capacity sold
    poi_limit: float      # MW, grid connection limit (both directions)
    wind_nom: float       # MW, co-located wind farm
    eff: float = 0.95     # one-way efficiency
    tau: float = 0.3      # s, inverter response time constant
    ramp: float = 20.0    # MW/s, ramp limit of the power conversion system


class Plant:
    """Battery and wind farm at one grid connection point. Positive power = export to grid."""

    def __init__(self, site, soc0):
        self.s, self.soc, self.p = site, soc0, 0.0

    def step(self, p_set, p_wind):
        s = self.s
        p_set = float(np.clip(p_set, -s.p_nom, s.p_nom))
        target = self.p + (p_set - self.p) * (1 - np.exp(-DT / s.tau))
        self.p += float(np.clip(target - self.p, -s.ramp * DT, s.ramp * DT))
        # hard plant protection at physical SOC limits
        if (self.soc <= 0.0 and self.p > 0) or (self.soc >= 1.0 and self.p < 0):
            self.p = 0.0
        e = self.p / s.eff if self.p > 0 else self.p * s.eff
        self.soc = float(np.clip(self.soc - e * DT / 3600 / s.e_nom, 0.0, 1.0))
        return dict(p_bess=self.p, p_poi=self.p + p_wind, soc=self.soc)


# ---------------------------------------------------------------- controllers under test
class SiteControllerV1:
    """First version. Priorities: plant limits, connection limit, FCR, schedule."""
    name = "Controller v1"
    soc_min, soc_max, deadband, full_dev = 0.10, 0.90, 0.010, 0.200
    sign, filter_tau, respect_poi = 1.0, 0.0, True
    reserve_headroom, soc_restore = False, False
    soc_reserve, restore_frac = 0.20, 0.2

    def __init__(self, site):
        self.s, self.p_fcr = site, 0.0

    def fcr_request(self, f):
        df = f - F0
        if abs(df) < self.deadband:
            return 0.0
        return -self.sign * self.s.fcr * float(np.clip(df / self.full_dev, -1, 1))

    def step(self, m):
        s = self.s
        req = self.fcr_request(m["f"])
        if self.filter_tau > 0:
            self.p_fcr += (req - self.p_fcr) * DT / self.filter_tau
        else:
            self.p_fcr = req
        sched = m["schedule"]
        if self.soc_restore:                       # bring SOC back towards the middle band
            if m["soc"] < self.soc_reserve:
                sched = -self.restore_frac * s.p_nom
            elif m["soc"] > 1 - self.soc_reserve:
                sched = self.restore_frac * s.p_nom
        if self.reserve_headroom:                  # keep room at the connection for full FCR
            sched = float(np.clip(sched, -s.poi_limit - m["p_wind"] + s.fcr, s.poi_limit - m["p_wind"] - s.fcr))
        p = self.p_fcr + sched
        # SOC management: the schedule gives way first, FCR only at the hard SOC bounds
        if m["soc"] <= self.soc_min:
            p = min(p, max(self.p_fcr, 0.0) if m["soc"] > 0.02 else 0.0)
        if m["soc"] >= self.soc_max:
            p = max(p, min(self.p_fcr, 0.0) if m["soc"] < 0.98 else 0.0)
        p = float(np.clip(p, -s.p_nom, s.p_nom))
        if self.respect_poi:
            p = float(np.clip(p, -s.poi_limit - m["p_wind"], s.poi_limit - m["p_wind"]))
        return p


class SiteController(SiteControllerV1):
    """Revised after testing: FCR headroom kept at the connection, SOC restored below 20%."""
    name = "Controller v2"
    reserve_headroom, soc_restore = True, True


class SignErrorController(SiteController):
    name = "v2 + fault: FCR sign error"
    sign = -1.0


class SlowFcrController(SiteController):
    name = "v2 + fault: filtered FCR (60 s)"
    filter_tau = 60.0


class NoPoiLimitController(SiteController):
    name = "v2 + fault: connection limit ignored"
    respect_poi, reserve_headroom = False, False


CONTROLLERS = [SiteControllerV1, SiteController, SignErrorController, SlowFcrController, NoPoiLimitController]
SITES = [Site("15 MW / 30 MWh (2 h)", 15, 30, fcr=10, poi_limit=15, wind_nom=12),
         Site("15 MW / 15 MWh (1 h)", 15, 15, fcr=10, poi_limit=15, wind_nom=12)]


# ---------------------------------------------------------------- scenarios
def scenario(name, dur, f, wind=None, schedule=None, soc0=0.5, delay=0.2, checks=()):
    n = int(dur / DT)
    t = np.arange(n) * DT
    return dict(name=name, t=t, f=f(t), wind=(wind(t) if wind else np.zeros(n)),
                schedule=(schedule(t) if schedule else np.zeros(n)), soc0=soc0, delay=delay, checks=checks)


def ou_noise(t, sigma=0.02, theta=1 / 30, seed=1):
    rng = np.random.default_rng(seed)
    x = np.zeros(len(t))
    for i in range(1, len(t)):
        x[i] = x[i - 1] - theta * x[i - 1] * DT + sigma * np.sqrt(2 * theta * DT) * rng.standard_normal()
    return F0 + x


def step_at(t0, value):
    return lambda t: np.where(t >= t0, value, 0.0)


SCENARIOS = [
    scenario("Normal frequency (noise)", 600, ou_noise, checks=("tracking", "direction", "soc")),
    scenario("Under-frequency step -200 mHz, 15 min", 1000, lambda t: F0 + step_at(60, -0.2)(t),
             checks=("activation", "sustain", "soc")),
    scenario("Over-frequency step +200 mHz, 15 min", 1000, lambda t: F0 + step_at(60, 0.2)(t),
             checks=("activation", "sustain", "soc")),
    scenario("Frequency ramp to 49.85 Hz", 300, lambda t: F0 - 0.15 * np.clip((t - 60) / 60, 0, 1),
             checks=("tracking", "direction")),
    scenario("Under-frequency step at low SOC", 400, lambda t: F0 + step_at(60, -0.2)(t), soc0=0.12,
             schedule=lambda t: np.full(len(t), 3.0), checks=("activation", "soc")),
    scenario("Wind surge at the connection limit", 400, ou_noise,
             wind=lambda t: 12.0 * np.clip((t - 60) / 30, 0.3, 1.0),
             schedule=lambda t: np.where(t > 100, 8.0, 0.0), checks=("poi", "tracking")),
    scenario("Step with 1 s communication delay", 300, lambda t: F0 + step_at(60, -0.2)(t), delay=1.0,
             checks=("activation",)),
    scenario("Schedule step to full discharge", 300, lambda t: np.full(len(t), F0),
             schedule=lambda t: np.where(t > 60, 5.0, 0.0), checks=("setpoint", "poi")),
]


# ---------------------------------------------------------------- run and evaluate
def run(ctrl_cls, site, sc):
    plant, ctrl = Plant(site, sc["soc0"]), ctrl_cls(site)
    n, lag = len(sc["t"]), max(1, int(round(sc["delay"] / DT)))
    out = {k: np.zeros(n) for k in ("p_set", "p_bess", "p_poi", "soc", "fcr_req")}
    meas = dict(f=F0, soc=sc["soc0"], p_wind=0.0, schedule=0.0)
    ref = SiteController(site)
    for i in range(n):
        j = max(0, i - lag)                                   # measurements arrive with a delay
        meas = dict(f=sc["f"][j], soc=out["soc"][j] if i >= lag else sc["soc0"],
                    p_wind=sc["wind"][j], schedule=sc["schedule"][i])
        p_set = ctrl.step(meas)
        r = plant.step(p_set, sc["wind"][i])
        out["p_set"][i], out["p_bess"][i], out["p_poi"][i], out["soc"][i] = p_set, r["p_bess"], r["p_poi"], r["soc"]
        out["fcr_req"][i] = ref.fcr_request(sc["f"][i])       # what the grid code asks for
    return out


def evaluate(site, sc, o, base):
    """base: the same run with the frequency held at 50 Hz, so delivered FCR = difference."""
    t, rows = sc["t"], []
    req, delivered = o["fcr_req"], o["p_bess"] - base["p_bess"]

    def add(check, value, limit, ok, unit):
        rows.append(dict(check=check, value=round(float(value), 3), limit=limit, unit=unit, passed=bool(ok)))

    if "activation" in sc["checks"]:
        after = t >= 60
        full = np.abs(req[after]).max()
        reached = np.where(np.abs(delivered[after]) >= 0.98 * full)[0]
        same_sign = np.sign(delivered[after][reached[0]]) == np.sign(req[after][-1]) if len(reached) else False
        t_act = (t[after][reached[0]] - 60) if len(reached) and same_sign else np.inf
        add("FCR full activation time", t_act, "<= 30", t_act <= 30, "s")
    if "sustain" in sc["checks"]:
        win = t >= 90
        share = np.mean(np.abs(delivered[win] - req[win]) <= 0.02 * site.fcr) * 100
        add("FCR sustained within 2% (15 min)", share, ">= 99", share >= 99, "% of time")
    if "tracking" in sc["checks"]:
        rmse = np.sqrt(np.mean((delivered - req) ** 2)) / site.fcr * 100
        add("FCR tracking error (RMS)", rmse, "<= 5", rmse <= 5, "% of FCR")
    if "direction" in sc["checks"]:
        c = np.corrcoef(delivered, req)[0, 1] if req.std() > 0 else 1.0
        add("FCR response direction (correlation)", c, ">= 0.9", c >= 0.9, "-")
    if "soc" in sc["checks"]:
        add("Lowest SOC", o["soc"].min() * 100, ">= 5", o["soc"].min() >= 0.05, "%")
    if "poi" in sc["checks"]:
        over = np.sum(np.abs(o["p_poi"]) > site.poi_limit * 1.01) * DT
        add("Time above connection limit", over, "<= 1", over <= 1, "s")
    if "setpoint" in sc["checks"]:
        target = sc["schedule"]
        settled = np.where((t > 60) & (np.abs(o["p_bess"] - target) <= 0.02 * site.p_nom))[0]
        ts = t[settled[0]] - 60 if len(settled) else np.inf
        add("Schedule settling time (2%)", ts, "<= 2", ts <= 2, "s")
    return rows


def main():
    t0 = time.time()
    rows, traces = [], {}
    for site in SITES:
        for sc in SCENARIOS:
            for C in CONTROLLERS:
                o = run(C, site, sc)
                base = run(C, site, dict(sc, f=np.full(len(sc["t"]), F0)))
                for r in evaluate(site, sc, o, base):
                    rows.append(dict(site=site.name, scenario=sc["name"], controller=C.name, **r))
                if site is SITES[0]:
                    traces[(sc["name"], C.name)] = o
    df = pd.DataFrame(rows)
    os.makedirs(RES, exist_ok=True)
    df.to_csv(os.path.join(RES, "part5_test_results.csv"), index=False)

    summary = (df.groupby("controller", sort=False)
                 .agg(checks=("passed", "size"), passed=("passed", "sum")).reset_index())
    summary["failed"] = summary.checks - summary.passed
    summary.to_csv(os.path.join(RES, "part5_test_summary.csv"), index=False)

    # Markdown report
    lines = ["# Closed-loop test report: battery site controller", "",
             f"{len(SITES)} site configurations x {len(SCENARIOS)} scenarios x {len(CONTROLLERS)} controller versions, "
             f"{len(df)} checks.", "", "## Summary", "", "| Controller | Checks | Passed | Failed |", "|---|---:|---:|---:|"]
    lines += [f"| {r.controller} | {r.checks} | {r.passed} | {r.failed} |" for r in summary.itertuples()]
    lines += ["", "## Failed checks", "", "| Site | Scenario | Controller | Check | Value | Limit |", "|---|---|---|---|---:|---|"]
    for r in df[~df.passed].itertuples():
        lines.append(f"| {r.site} | {r.scenario} | {r.controller} | {r.check} | {r.value} {r.unit} | {r.limit} |")
    with open(os.path.join(RES, "part5_test_report.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")

    pd.set_option("display.width", 200)
    print(summary.to_string(index=False))
    print(df[(df.controller == SiteController.name)][["site", "scenario", "check", "value", "unit", "passed"]].to_string(index=False))

    # Figure 1: under-frequency step, reference vs slow and sign-error controllers
    name = SCENARIOS[1]["name"]
    t = SCENARIOS[1]["t"]
    fig, ax = plt.subplots(2, 1, figsize=(6.0, 6.4), gridspec_kw={'height_ratios': [1, 1.25]})
    ref = traces[(name, SiteController.name)]
    ax[0].plot(t, ref["fcr_req"], color=OI['black'], lw=1, ls=':', label="Required by grid code")
    styles = [(SiteController.name, OI['blue'], '-'), (SlowFcrController.name, OI['orange'], '--'),
              (SignErrorController.name, OI['vermillion'], '-.')]
    for cname, c, ls in styles:
        ax[0].plot(t, traces[(name, cname)]["p_bess"], color=c, ls=ls, lw=1.1, label=cname)
    ax[0].axvline(90, color='grey', lw=0.7, ls=':')
    ax[0].text(92, -9.5, "30 s limit", fontsize=7, color='grey')
    ax[0].set(xlim=(40, 200), xlabel="Time (s)", ylabel="Battery power (MW)")
    ax[0].legend(frameon=False, loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=2)
    ax[0].text(-0.1, 1.2, "A", transform=ax[0].transAxes, fontsize=11, fontweight='bold')

    # Figure panel B: pass/fail matrix (reference site)
    sub = df                                  # a cell passes only if every check passes on both sites
    mat = sub.groupby(["scenario", "controller"], sort=False).passed.all().unstack()[[C.name for C in CONTROLLERS]]
    mat = mat.reindex([s["name"] for s in SCENARIOS])
    from matplotlib.colors import ListedColormap
    ax[1].imshow(mat.values.astype(int), cmap=ListedColormap([OI['orange'], OI['sky']]), aspect="auto")
    for (i, j), v in np.ndenumerate(mat.values):
        ax[1].text(j, i, "pass" if v else "FAIL", ha="center", va="center", fontsize=6.5,
                   fontweight='normal' if v else 'bold')
    ax[1].set_xticks(range(len(CONTROLLERS)), ["v1", "v2", "Sign\nerror", "Filtered\nFCR", "No\nlimit"], fontsize=7)
    ax[1].set_xlabel("Controller version (last three: faults injected into v2)")
    ax[1].set_yticks(range(len(SCENARIOS)), mat.index, fontsize=7)
    ax[1].tick_params(length=0)
    for sp in ax[1].spines.values():
        sp.set_visible(False)
    ax[1].text(-0.62, 1.03, "B", transform=ax[1].transAxes, fontsize=11, fontweight='bold')
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig1_closed_loop_tests.png"))
    plt.close(fig)
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
