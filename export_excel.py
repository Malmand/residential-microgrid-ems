"""
Collect every CSV in results/ into one formatted Excel workbook.

Run after any of the part scripts:
    python export_excel.py
Output:
    results/microgrid_results.xlsx

Only the CSVs that exist are included, so the workbook always reflects the
latest runs. Summary tables come first, then the time series.
"""
import os
from datetime import datetime

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
OUT = os.path.join(RESULTS, "microgrid_results.xlsx")

# (csv file, sheet name, description, transpose one-row summaries)
SHEETS = [
    ("part1_controller_comparison.csv", "P1 Controllers",
     "Part 1: rule-based vs MILP controller, 24 h at 1.5 min, scenarios S1-S4", False),
    ("part2_planning_summary.csv", "P2 Planning",
     "Part 2: planning MILP summary for the fixed system (10 kWp PV, 40 kWh battery)", True),
    ("part2_sizing_base_summary.csv", "P2 Sizing base",
     "Part 2: sizing LP, base case (CAPEX scaled to the simulated period + OPEX)", True),
    ("part2_sizing_isolated_summary.csv", "P2 Sizing isolated",
     "Part 2: sizing LP, isolated microgrid", True),
    ("part2_sizing_no_export_payment_summary.csv", "P2 Sizing no export pay",
     "Part 2: sizing LP, export not paid", True),
    ("part2_sizing_capex_budget_summary.csv", "P2 Sizing CAPEX budget",
     "Part 2: sizing LP, CAPEX budget", True),
    ("part3_forecast_metrics.csv", "P3 Forecast metrics",
     "Part 3: day-ahead forecast accuracy on the test period (1 Oct - 31 Dec 2021)", False),
    ("part3_mpc_summary.csv", "P3 MPC",
     "Part 3: realised cost of the forecast-driven MPC vs a perfect-forecast plan", False),
    ("part4_siting.csv", "P4 Siting",
     "Part 4: 60 kWp PV plant at three connection points on the LV feeder, after grid coupling", False),
    ("part4_coupling_iterations.csv", "P4 Coupling iterations",
     "Part 4: optimisation and load-flow iterations until the schedule is network-feasible", False),
    ("part5_test_summary.csv", "P5 Test summary",
     "Part 5: closed-loop tests of a battery site controller, checks passed per controller version", False),
    ("part5_test_results.csv", "P5 Test results",
     "Part 5: every check per site, scenario and controller version", False),
    ("part2_planning_timeseries.csv", "P2 Planning series",
     "Part 2: planning dispatch, 15-min time series", False),
    ("part3_forecasts_test.csv", "P3 Forecast series",
     "Part 3: measured values and day-ahead forecasts, 15-min, test period", False),
]

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=12)
THIN = Side(style="thin", color="BFBFBF")


def _format(ws, df, first_row):
    """Header style, number formats, column widths and frozen header."""
    for c in range(1, df.shape[1] + 1):
        cell = ws.cell(row=first_row, column=c)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN)
    ws.row_dimensions[first_row].height = 32

    for c, col in enumerate(df.columns, start=1):
        letter = get_column_letter(c)
        numeric = pd.api.types.is_numeric_dtype(df[col])
        is_time = pd.api.types.is_datetime64_any_dtype(df[col])
        fmt = "yyyy-mm-dd hh:mm" if is_time else ("#,##0.00" if numeric else None)
        if fmt and len(df) < 50_000:
            for r in range(first_row + 1, first_row + 1 + len(df)):
                ws.cell(row=r, column=c).number_format = fmt
        width = max(len(str(col)), *(len(str(v)) for v in df[col].head(200))) + 2
        ws.column_dimensions[letter].width = min(max(width, 10), 45 if not is_time else 18)
    ws.freeze_panes = ws.cell(row=first_row + 1, column=1)


def main():
    present = [s for s in SHEETS if os.path.exists(os.path.join(RESULTS, s[0]))]
    if not present:
        raise SystemExit("No CSV found in results/. Run the part scripts first.")

    with pd.ExcelWriter(OUT, engine="openpyxl") as xw:
        # Contents sheet
        contents = pd.DataFrame(
            [(name, desc, f) for f, name, desc, _ in present],
            columns=["Sheet", "Content", "Source CSV"])
        contents.to_excel(xw, sheet_name="Contents", index=False, startrow=2)
        ws = xw.sheets["Contents"]
        ws["A1"] = "Residential microgrid EMS: results workbook"
        ws["A1"].font = TITLE_FONT
        ws["A2"] = f"Generated {datetime.now():%Y-%m-%d %H:%M} from results/*.csv"
        _format(ws, contents, first_row=3)

        for f, name, desc, transpose in present:
            df = pd.read_csv(os.path.join(RESULTS, f))
            for col in df.columns:
                if col.lower() in ("datetime", "unnamed: 0") or "date" in col.lower():
                    parsed = pd.to_datetime(df[col], errors="coerce")
                    if parsed.notna().mean() > 0.9:
                        df[col] = parsed
            df = df.rename(columns={"Unnamed: 0": "datetime"})
            if transpose and len(df) == 1:
                df = df.T.reset_index()
                df.columns = ["Metric", "Value"]
            df.to_excel(xw, sheet_name=name, index=False, startrow=2)
            ws = xw.sheets[name]
            ws["A1"] = desc
            ws["A1"].font = TITLE_FONT
            _format(ws, df, first_row=3)

    print(f"Saved {os.path.relpath(OUT, HERE)} with {len(present) + 1} sheets")


if __name__ == "__main__":
    main()
