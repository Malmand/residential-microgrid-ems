"""
compare_controllers.py — run all scenarios with both controllers and save a cost table.
"""
import os
import numpy as np
import simulator as run
from param import delta_t, C_bss, C_ev

import rule_based as RB
import milp_controller as opt_V2

FUEL_PRICE = 0.30
GRID_PRICE = 0.25
SCENARIOS  = ['S1', 'S2', 'S3', 'S4']

CONTROLLERS = {
    'RB' : RB.control_decision,
    'OPT': opt_V2.control_decision,
}

rows = []

for sc in SCENARIOS:
    for ctrl_name, ctrl_fn in CONTROLLERS.items():

        # Reset OPT model between runs
        if ctrl_name == 'OPT':
            opt_V2._model  = None
            opt_V2._solver = None

        results = run.run_sim(ctrl_fn, sc, verbose=False)

        gen_energy   = results.P_gen.sum() * delta_t
        pv_used      = results.P_pv.sum() * delta_t
        pv_curtailed = (results.P_pv_max - results.P_pv).clip(0).sum() * delta_t
        load_energy  = results.P_load.sum() * delta_t
        hp_energy    = results.P_hp.sum() * delta_t
        total_demand = load_energy + hp_energy

        true_cost    = gen_energy * FUEL_PRICE
        virtual_cost = (total_demand - pv_used + gen_energy) * GRID_PRICE

        # EV departure SOC
        ev_conn = np.array(results.EV_connected)
        dep_idx = np.where(np.diff(ev_conn.astype(int)) < 0)[0]
        soc_dep = (results.SOC_ev[dep_idx[0]+1] / C_ev * 100) if len(dep_idx) else (results.SOC_ev[-1]/C_ev*100)

        rows.append({
            'Scenario'        : sc,
            'Controller'      : ctrl_name,
            'Gen (kWh)'       : round(gen_energy,   2),
            'PV used (kWh)'   : round(pv_used,       2),
            'PV curtailed(kWh)': round(pv_curtailed, 2),
            'Total demand(kWh)': round(total_demand, 2),
            'True cost (€)'   : round(true_cost,     2),
            'Virtual cost (€)': round(virtual_cost,  2),
            'EV SOC dep (%)'  : round(soc_dep,       1),
            'BSS final (%)'   : round(results.SOC_bss[-1]/C_bss*100, 1),
        })

        print(f"  {sc} {ctrl_name} done — gen={gen_energy:.2f} kWh, "
              f"true={true_cost:.2f}€, virtual={virtual_cost:.2f}€")

# ── Print table ────────────────────────────────────────────────────────────
print("\n" + "="*90)
print("  FULL RESULTS TABLE — ALL SCENARIOS × CONTROLLERS")
print("="*90)

header = f"{'Sc':<4} {'Ctrl':<5} {'Gen':>8} {'PV used':>9} {'PV curt':>8} "
header += f"{'Demand':>8} {'True€':>7} {'Virtual€':>9} {'EV dep%':>8} {'BSS%':>6}"
print(header)
print("-"*90)

for r in rows:
    print(
        f"{r['Scenario']:<4} {r['Controller']:<5} "
        f"{r['Gen (kWh)']:>8.2f} {r['PV used (kWh)']:>9.2f} "
        f"{r['PV curtailed(kWh)']:>8.2f} {r['Total demand(kWh)']:>8.2f} "
        f"{r['True cost (€)']:>7.2f} {r['Virtual cost (€)']:>9.2f} "
        f"{r['EV SOC dep (%)']:>8.1f} {r['BSS final (%)']:>6.1f}"
    )

print("="*90)

# ── Save CSV ───────────────────────────────────────────────────────────────
import csv
out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'results')
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, 'part1_controller_comparison.csv')
with open(out_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
print(f"\nSaved: {out_path}")