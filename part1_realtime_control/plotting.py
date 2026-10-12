# plotting.py
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from param import *

EPS = 1e-9

_START_HOUR       = 10.5
_MIDNIGHT_ELAPSED = 24 - _START_HOUR


# =============================================================================
# SHARED STYLE HELPERS
# =============================================================================

def _time_fmt(x, pos):
    h = int(x) % 24
    return f"{h:02d}:00"


def _apply_xaxis(ax):
    ax.set_xticks(np.arange(12, 35, 2))
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_time_fmt))
    ax.set_xlabel('Time of day', fontsize=8.5)
    ax.set_xlim(10.5, 34.5)


def _style(ax, ylabel, title):
    ax.set_title(title, fontsize=10, fontweight='bold', pad=30)
    ax.set_ylabel(ylabel, fontsize=8.5, labelpad=6)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    _apply_xaxis(ax)


def _rcparams():
    plt.rcParams.update({
        'font.family'      : 'sans-serif',
        'axes.grid'        : False,
        'xtick.direction'  : 'out',
        'ytick.direction'  : 'out',
        'font.size'        : 9,
        'axes.spines.top'  : False,
        'axes.spines.right': False,
        'axes.linewidth'   : 0.6,
        'xtick.major.width': 0.6,
        'ytick.major.width': 0.6,
        'grid.linewidth'   : 0.4,
        'grid.color'       : '#cccccc',
        'legend.frameon'   : False,
        'legend.fontsize'  : 8,
        'figure.dpi'       : 150,
    })


_C = dict(
    PV   = '#E69F00',
    LOAD = '#000000',
    HP   = '#D55E00',
    GEN  = '#CC79A7',
    BSS  = '#009E73',
    EV   = '#0072B2',
    GREY = '#777777',
    PVD  = '#B87300',
)


# =============================================================================
# CHECK CONSTRAINTS
# =============================================================================

def check_res(res):
    eps    = 1e-3
    errors = 0
    for t in range(time_steps):
        balance = (res.P_pv[t] + res.P_gen[t]
                   - res.P_bss[t] - res.P_ev[t]
                   - res.P_load[t] - abs(res.P_hp[t]))
        if abs(balance) >= eps:
            print(f'  ✗ Power balance {balance:+.4f} kW at t={t}')
            errors += 1
        if res.P_pv[t] < -eps or res.P_pv[t] > res.P_pv_max[t] + eps:
            print(f'  ✗ PV out of bounds ({res.P_pv[t]:.2f} kW) at t={t}')
            errors += 1
        if res.P_gen[t] > eps:
            if res.P_gen[t] < P_min_gen - eps or res.P_gen[t] > P_max_gen + eps:
                print(f'  ✗ Generator out of bounds ({res.P_gen[t]:.2f} kW) at t={t}')
                errors += 1
        if abs(res.P_bss[t]) > P_nom_bss + eps:
            print(f'  ✗ BSS power exceeded ({res.P_bss[t]:.2f} kW) at t={t}')
            errors += 1
        if (res.SOC_bss[t] < SOC_min_bss * C_bss - eps
                or res.SOC_bss[t] > SOC_max_bss * C_bss + eps):
            print(f'  ✗ BSS SOC out of bounds ({(res.SOC_bss[t]/C_bss)*100:.1f}%) at t={t}')
            errors += 1
        if abs(res.P_ev[t]) > P_nom_ev + eps:
            print(f'  ✗ EV power exceeded ({res.P_ev[t]:.2f} kW) at t={t}')
            errors += 1
        if (res.SOC_ev[t] < SOC_min_ev * C_ev - eps
                or res.SOC_ev[t] > SOC_max_ev * C_ev + eps):
            print(f'  ✗ EV SOC out of bounds ({(res.SOC_ev[t]/C_ev)*100:.1f}%) at t={t}')
            errors += 1
    if errors == 0:
        print("✅ Constraints Check: PASSED with zero violations.")
    else:
        print(f"❌ Constraints Check: FAILED with {errors} violation(s).")


# =============================================================================
# PRINT SUMMARY
# =============================================================================

def print_res(res):
    print("\n" + "=" * 45)
    print("   MICROGRID SIMULATION SUMMARY (24h)")
    print("=" * 45)
    print(f"  House load demand :  {np.sum(res.P_load)*delta_t:6.2f} kWh")
    print(f"  Heat pump demand  :  {np.sum(res.P_hp)*delta_t:6.2f} kWh")
    print(f"  PV energy used    :  {np.sum(res.P_pv)*delta_t:6.2f} kWh")
    print(f"  Generator energy  :  {np.sum(res.P_gen)*delta_t:6.2f} kWh")
    print("-" * 45)
    print(f"  Final battery SOC :  {(res.SOC_bss[-1]/C_bss)*100:5.1f} %")
    print(f"  Final EV SOC      :  {(res.SOC_ev[-1]/C_ev)*100:5.1f} %")

    # EV charging outcome
    ev_conn = np.array(res.EV_connected) if hasattr(res, 'EV_connected') else np.zeros(time_steps)
    conn_steps = int(ev_conn.sum())
    if conn_steps > 0:
        dep_idx = np.where(np.diff(ev_conn.astype(int)) < 0)[0]
        soc_dep = (res.SOC_ev[dep_idx[0]+1] / C_ev * 100) if len(dep_idx) else (res.SOC_ev[-1]/C_ev*100)
        target  = SOC_target_ev * 100
        reached = soc_dep >= target - 1.0
        print(f"  EV connected for  :  {conn_steps*delta_t:5.1f} h")
        print(f"  EV SOC at depart. :  {soc_dep:5.1f} %  (target {target:.0f} %)")
        print(f"  EV target reached :  {'✓ Yes' if reached else '✗ No'}")
    print("=" * 45 + "\n")


# =============================================================================
# PLOTS
# =============================================================================

def _panel_labels(axes):
    for ax, lab in zip(axes, 'ABCD'):
        ax.text(-0.06, 1.0, lab, transform=ax.transAxes, fontsize=11,
                fontweight='bold', va='bottom', ha='right')


def _save(fig, save_dir, name, sc):
    """Save figure and print confirmation."""
    path = f"{save_dir}/{name}_{sc}.jpg"
    fig.savefig(path, dpi=200, bbox_inches='tight', pil_kwargs={'quality': 95})
    print(f"  Saved: {path}")


def plot_res(res, scenario='', save_dir='.'):
    """
    Five publishable figures, saved as JPGs.

    Args:
        res      : simulation results object
        scenario : scenario label e.g. 'S3' — appears in titles and filenames
        save_dir : folder to save JPGs (created if it doesn't exist)

    Usage:
        plot_res(results, scenario='S3', save_dir='figures')
    Files saved:
        figures/fig1_power_dispatch_S3.jpg
        figures/fig2_solar_utilisation_S3.jpg
        figures/fig3_storage_S3.jpg
        figures/fig4_energy_flows_S3.jpg
        figures/fig5_thermal_comfort_S3.jpg
    """
    import os
    os.makedirs(save_dir, exist_ok=True)
    sc = str(scenario)   # e.g. 'S3' — used in titles and filenames

    _rcparams()

    PV   = _C['PV'];   LOAD = _C['LOAD']; HP  = _C['HP']
    GEN  = _C['GEN'];  BSS  = _C['BSS'];  EV  = _C['EV']
    GREY = _C['GREY']; PVD  = _C['PVD']

    # ── time vector (10:30 → 34:30, labels wrap to HH:00 via _time_fmt) ───────
    t = np.linspace(10.5, 34.5, len(res.t), endpoint=False)

    # raw signals
    P_pv     = res.P_pv
    P_pv_max = res.P_pv_max
    P_load   = res.P_load
    P_hp     = res.P_hp
    P_gen    = res.P_gen
    P_bss    = res.P_bss
    P_ev     = res.P_ev
    SOC_bss  = res.SOC_bss
    SOC_ev   = res.SOC_ev
    T_hp     = res.T_hp.astype(float)
    T_set    = res.T_set.astype(float)

    # derived
    bss_chg     = np.where(P_bss > 0,  P_bss, 0.0)
    bss_dis     = np.where(P_bss < 0, -P_bss, 0.0)
    ev_chg      = np.where(P_ev  > 0,  P_ev,  0.0)
    ev_dis      = np.where(P_ev  < 0, -P_ev,  0.0)
    soc_bss     = (SOC_bss / C_bss) * 100
    soc_ev      = (SOC_ev  / C_ev)  * 100
    base_demand = P_load + np.maximum(P_hp, 0.0)
    cum         = lambda p: np.cumsum(p * delta_t)
    err         = T_hp - T_set

    # EV connection window (for SOC annotation)
    ev_conn = np.array(res.EV_connected) if hasattr(res, 'EV_connected') else np.zeros(len(t))

    # =========================================================================
    # FIGURE 1 — Power dispatch + storage support
    # =========================================================================
    fig, axes = plt.subplots(
        2, 1, figsize=(10, 6.2), sharex=True,
        constrained_layout=True,
        gridspec_kw={'height_ratios': [2.2, 1.2]}
    )

    # -------------------------------------------------------------------------
    # Top panel: main microgrid balance
    # -------------------------------------------------------------------------
    ax = axes[0]

    essential_demand = P_load + np.maximum(P_hp, 0.0)
    total_demand = essential_demand + bss_chg + ev_chg
    total_supply = P_pv + P_gen + bss_dis + ev_dis

    # Essential demand as filled grey area
    ax.fill_between(
        t, essential_demand, 0,
        color='#BDBDBD',
        alpha=0.60,
        label='Essential demand'
    )

    # PV used and PV available
    ax.fill_between(
        t, P_pv, 0,
        color=PV,
        alpha=0.45,
        label='PV used'
    )

    ax.plot(
        t, P_pv_max,
        color=PVD,
        lw=1.4,
        ls='--',
        label='PV available'
    )

    # Total demand and total supply
    ax.plot(
        t, total_demand,
        color='black',
        lw=1.0,
        label='Total demand',
        zorder=3
    )
    # Total supply equals total demand by the power balance (checked in
    # check_res), so it is not drawn on top of the demand line.

    # Optional generator fill if generator is used
    if np.max(P_gen) > EPS:
        ax.fill_between(
            t, P_gen, 0,
            color=GEN,
            alpha=0.35,
            label='Generator'
        )

    vals = np.concatenate([essential_demand, total_demand, total_supply, P_pv_max])
    vals = vals[vals > EPS]
    if len(vals):
        ax.set_ylim(0, np.percentile(vals, 98) * 1.20)
    else:
        ax.set_ylim(0, 1)

    ax.set_ylabel('Power (kW)', fontsize=8.5)
    ax.set_title(
        f'Power dispatch, scenario {sc}',
        fontsize=11,
        fontweight='bold',
        pad=50
    )

    ax.legend(
        loc='lower center',
        bbox_to_anchor=(0.5, 1.02),
        ncol=5,
        frameon=False,
        fontsize=8
    )

    # -------------------------------------------------------------------------
    # Bottom panel: storage behaviour
    # -------------------------------------------------------------------------
    ax = axes[1]

    ax.fill_between(t,  bss_chg, 0, color=BSS, alpha=0.60, label='Battery charge')
    ax.fill_between(t, -bss_dis, 0, color=BSS, alpha=0.35, label='Battery discharge')
    ax.fill_between(t,  ev_chg,  0, color=EV,  alpha=0.60, hatch='//', label='EV charge')
    ax.fill_between(t, -ev_dis,  0, color=EV,  alpha=0.35, hatch='//', label='EV discharge')

    ax.axhline(0, color='black', lw=0.8)
    ax.set_ylabel('Storage power (kW)\ncharging > 0', fontsize=8.5)

    ax.legend(
        loc='lower center',
        bbox_to_anchor=(0.5, 1.02),
        ncol=4,
        frameon=False,
        fontsize=8
    )

    _apply_xaxis(ax)

    _panel_labels(axes)
    _save(fig, save_dir, 'fig1_power_dispatch', sc)
    plt.close(fig)

    # =========================================================================
    # FIGURE 2 — Solar utilisation vs demand
    # =========================================================================
    fig, ax = plt.subplots(figsize=(10, 3.6), constrained_layout=True)

    _w        = max(1, len(t) // 120)
    pv_smooth = np.convolve(P_pv_max, np.ones(_w) / _w, mode='same')
    ax.fill_between(t, P_pv, 0, color=PV, alpha=0.85, label='PV utilised')
    ax.fill_between(t, pv_smooth, P_pv,
                    where=(pv_smooth > P_pv),
                    color=PV, alpha=0.22, hatch='////', label='PV unused')
    ax.plot(t, base_demand, color=LOAD, lw=0.9,
            label='Base demand (load + HP)', zorder=4)
    ax.axhline(0, color='black', lw=0.6)
    ax.set_ylim(0, max(np.max(pv_smooth), np.max(base_demand)) * 1.15)
    _style(ax, 'Power (kW)', f'Solar utilisation and demand coverage, scenario {sc}')
    ax.legend(loc='upper right', ncol=3)

    _save(fig, save_dir, 'fig2_solar_utilisation', sc)
    plt.close(fig)

    # =========================================================================
    # FIGURE 3 — Storage power + SOC
    # =========================================================================
    fig, axes = plt.subplots(
        2, 1, figsize=(10, 5.5), sharex=True,
        constrained_layout=True,
        gridspec_kw={'height_ratios': [1, 1]}
    )

    ax = axes[0]
    ax.fill_between(t,  bss_chg, 0, color=BSS, alpha=0.60, label='Battery charge')
    ax.fill_between(t, -bss_dis, 0, color=BSS, alpha=0.35, label='Battery discharge')
    ax.fill_between(t,  ev_chg,  0, color=EV,  alpha=0.60, hatch='//', label='EV charge')
    ax.fill_between(t, -ev_dis,  0, color=EV,  alpha=0.35, hatch='//', label='EV discharge')
    ax.plot(t,  bss_chg, color=BSS, lw=0.8, alpha=0.7)
    ax.plot(t, -bss_dis, color=BSS, lw=0.8, alpha=0.7)
    ax.plot(t,  ev_chg,  color=EV,  lw=0.8, alpha=0.7)
    ax.plot(t, -ev_dis,  color=EV,  lw=0.8, alpha=0.7)
    ax.axhline(0, color='black', lw=0.8)
    all_s = np.concatenate([bss_chg, bss_dis, ev_chg, ev_dis])
    nz    = all_s[all_s > 0]
    if len(nz):
        q75s, q25s = np.percentile(nz, [75, 25])
        y_mag = max((q75s + 2.0*(q75s - q25s)) * 1.3, 1.5)
    else:
        y_mag = 1.5
    ax.set_ylim(-y_mag, y_mag)
    ax.set_ylabel('Power (kW)\ncharging > 0', fontsize=8.5)
    ax.set_title(f'Storage power and state of charge, scenario {sc}',
                 fontsize=10, fontweight='bold', pad=30)
    ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.02),
              ncol=4, fontsize=7.5, frameon=False)

    ax = axes[1]
    # shade EV connection window
    ax.fill_between(t, 0, 105, where=(ev_conn > 0.5),
                    color=EV, alpha=0.07, label='EV connected')
    ax.plot(t, soc_bss, color=BSS, lw=2.0, label='Battery SOC')
    ax.plot(t, soc_ev,  color=EV,  lw=2.0, ls='--', label='EV SOC')
    ax.axhline(SOC_min_bss   * 100, color=BSS, lw=0.9, ls=':',
               label=f'BSS min ({SOC_min_bss*100:.0f}%)')
    ax.axhline(SOC_max_bss   * 100, color=BSS, lw=0.9, ls='-.', alpha=0.6,
               label=f'BSS max ({SOC_max_bss*100:.0f}%)')
    ax.axhline(SOC_target_ev * 100, color=EV,  lw=0.9, ls='-.', alpha=0.6,
               label=f'EV target ({SOC_target_ev*100:.0f}%)')

    # annotate SOC at departure
    dep_idx = np.where(np.diff((ev_conn > 0.5).astype(int)) < 0)[0]
    if len(dep_idx):
        t_dep      = t[dep_idx[0] + 1]
        soc_at_dep = soc_ev[dep_idx[0] + 1]
        target_pct = SOC_target_ev * 100
        reached    = soc_at_dep >= target_pct - 1.0
        col        = BSS if reached else HP
        sym        = '✓' if reached else '✗'
        ax.axvline(t_dep, color=EV, lw=1.0, ls=':', alpha=0.7)
        ax.annotate(f"{sym} Departure SOC: {soc_at_dep:.0f}%",
                    xy=(t_dep, soc_at_dep),
                    xytext=(t_dep + 0.8, min(soc_at_dep + 8, 98)),
                    fontsize=7.5, color=col, fontweight='bold',
                    arrowprops=dict(arrowstyle='->', color=col, lw=0.8))

    ax.set_ylim(0, 105)
    ax.set_ylabel('SOC (%)', fontsize=8.5)
    ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.02),
              ncol=4, fontsize=7.5, frameon=False)
    _apply_xaxis(ax)

    _panel_labels(axes)
    _save(fig, save_dir, 'fig3_storage', sc)
    plt.close(fig)

    # =========================================================================
    # FIGURE 4 — Cumulative energy flows
    # =========================================================================
    fig, ax = plt.subplots(figsize=(10, 3.6), constrained_layout=True)

    ax.stackplot(t,
                 cum(P_pv), cum(bss_dis), cum(ev_dis), cum(P_gen),
                 labels=['PV', 'Battery out', 'EV out (V2H)', 'Generator'],
                 colors=[PV, BSS, EV, GEN], alpha=0.85)
    ax.stackplot(t,
                 -cum(P_load), -cum(P_hp), -cum(bss_chg), -cum(ev_chg),
                 labels=['House load', 'Heat pump', 'Battery charged', 'EV charged'],
                 colors=['#444444', HP, BSS, EV], alpha=0.55)
    ax.axhline(0, color='black', lw=1.2)
    _style(ax, 'Cumulative energy (kWh)',
           f'Cumulative energy flows (supply above zero, demand below), scenario {sc}')
    ax.title.set_position((0.5, 1.0))
    ax.set_title(ax.get_title(), fontsize=10, fontweight='bold', pad=48)
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles, labels, loc='lower center', bbox_to_anchor=(0.5, 1.02),
              ncol=4, fontsize=7.5, frameon=False)

    _save(fig, save_dir, 'fig4_energy_flows', sc)
    plt.close(fig)

    # =========================================================================
    # FIGURE 5 — Thermal comfort + tracking error
    # =========================================================================
    fig, axes = plt.subplots(
        2, 1, figsize=(10, 5.0), sharex=True,
        constrained_layout=True,
        gridspec_kw={'height_ratios': [2, 1]}
    )

    ax = axes[0]
    ax.fill_between(t, T_set - 0.5, T_set + 0.5,
                    color=GREY, alpha=0.20, label='Comfort band (±0.5 °C)', zorder=1)
    ax.plot(t, T_set, color=LOAD, lw=1.6, ls='--', label='Setpoint',    zorder=2)
    ax.plot(t, T_hp,  color=HP,   lw=2.0,          label='Indoor temp', zorder=3)
    # The simulation starts from 0 °C, so the first minutes are a warm-up
    # transient; the axis is limited to the comfort range and the start noted.
    ax.set_ylim(np.min(T_set) - 3.0, np.max([np.max(T_hp), np.max(T_set)]) + 1.0)
    if T_hp[0] < np.min(T_set) - 3.0:
        ax.text(t[0] + 0.15, np.min(T_set) - 2.6, f'Warm-up from {T_hp[0]:.0f} °C',
                fontsize=7, color=GREY, va='bottom')
    ax.set_ylabel('Temperature (°C)', fontsize=8.5)
    ax.set_title(f'Thermal comfort, scenario {sc}',
                 fontsize=10, fontweight='bold', pad=30)
    ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.02),
              ncol=3, fontsize=7.5, frameon=False)

    ax = axes[1]
    ax.fill_between(t, err, 0, where=err >= 0, color=HP, alpha=0.55, label='Above setpoint')
    ax.fill_between(t, err, 0, where=err <  0, color=EV, alpha=0.55, label='Below setpoint')
    ax.plot(t, err, color='#222222', lw=0.9)
    ax.axhline( 0.5, color=GREY,    lw=0.8, ls='--')
    ax.axhline(-0.5, color=GREY,    lw=0.8, ls='--')
    ax.axhline(0,    color='black', lw=0.8)
    ax.set_ylim(-3.0, max(3.0, np.max(err) + 1.0))
    ax.set_ylabel('Error (°C)', fontsize=8.5)
    ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.02),
              ncol=2, fontsize=7.5, frameon=False)
    _apply_xaxis(ax)

    _panel_labels(axes)
    _save(fig, save_dir, 'fig5_thermal_comfort', sc)
    plt.close(fig)

def print_costs(results, delta_t, fuel_price=0.3):
    gen_energy  = results.P_gen.sum() * delta_t
    true_cost   = gen_energy * fuel_price
    pv_curtail  = (results.P_pv_max - results.P_pv).clip(0).sum() * delta_t

    # SOC stored in kWh — divide by capacity for fraction
    soc_bss_init  = results.SOC_bss[0]  / C_bss
    soc_ev_init   = results.SOC_ev[0]   / C_ev
    soc_bss_final = results.SOC_bss[-1] / C_bss
    soc_ev_final  = results.SOC_ev[-1]  / C_ev

    print("=" * 45)
    print("  TRUE OPERATIONAL COST")
    print("=" * 45)
    print(f"  Generator energy    : {gen_energy:.2f} kWh")
    print(f"  True cost           : {true_cost:.2f} €")
    print(f"  PV curtailed        : {pv_curtail:.4f} kWh")
    print(f"  Battery SOC  initial: {soc_bss_init*100:.1f} %")
    print(f"  Battery SOC  final  : {soc_bss_final*100:.1f} %")
    print(f"  EV SOC       initial: {soc_ev_init*100:.1f} %")
    print(f"  EV SOC       final  : {soc_ev_final*100:.1f} %")
    print("=" * 45)