"""
Day-ahead forecasting of household load and PV production with XGBoost.

Forecasts are issued at midnight for the next 96 quarter-hours, so every
feature used for a target at time t only looks at data from the previous day
or earlier (lags of at least 96 steps). This keeps the setup leakage-free and
usable by the day-ahead MPC in mpc.py.

Two baselines are reported for comparison:
    - persistence: the value at the same time on the previous day
    - 7-day profile: mean of the same quarter-hour over the previous 7 days

Chronological split (2021 data, 15-min resolution):
    train      : 15 Jan - 31 Aug (first two weeks dropped to fill the lags)
    validation : 1 - 30 Sep      (early stopping only)
    test       : 1 Oct - 31 Dec  (never seen during training or tuning)

Usage:
    python forecasting.py
Outputs:
    ../results/part3_forecast_metrics.csv
    ../results/part3_forecasts_test.csv   (used by mpc.py)
    figures/forecast_test_week.png / .pdf
"""
import os

import numpy as np
import pandas as pd
import xgboost as xgb

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "..", "data", "annual", "annual_profiles_2021.csv")
RESULTS_DIR = os.path.join(HERE, "..", "results")
FIG_DIR = os.path.join(HERE, "figures")

STEPS_PER_DAY = 96          # 15-minute resolution
YEARLY_KWH = 10_000         # same household scaling as part 2

TRAIN_END = "2021-08-31 23:45"
VAL_END = "2021-09-30 23:45"


# =============================================================================
# DATA
# =============================================================================

def load_profiles():
    df = pd.read_csv(DATA_PATH, sep=";", index_col="DateTime", parse_dates=True)
    out = pd.DataFrame(index=df.index)
    out["load_kw"] = np.clip(df["Load"].values, 0, None) * YEARLY_KWH   # kW
    out["pv_pu"] = np.clip(df["PV"].values, 0, None)                    # kW per kWp
    return out


# =============================================================================
# FEATURES (all lags >= 1 day, so valid for a forecast issued at midnight)
# =============================================================================

def make_features(series: pd.Series) -> pd.DataFrame:
    idx = series.index
    X = pd.DataFrame(index=idx)

    # Calendar features
    qh = idx.hour * 4 + idx.minute // 15
    X["qh_sin"] = np.sin(2 * np.pi * qh / STEPS_PER_DAY)
    X["qh_cos"] = np.cos(2 * np.pi * qh / STEPS_PER_DAY)
    X["dow"] = idx.dayofweek
    X["weekend"] = (idx.dayofweek >= 5).astype(int)
    # No day-of-year feature: the test months (Oct-Dec) are not in the training
    # period, so the seasonal level is carried by the lag features instead.

    # Same quarter-hour on previous days
    for d in range(1, 8):
        X[f"lag_{d}d"] = series.shift(d * STEPS_PER_DAY)
    lag_cols = [f"lag_{d}d" for d in range(1, 8)]
    X["same_qh_mean_7d"] = X[lag_cols].mean(axis=1)
    X["same_qh_std_7d"] = X[lag_cols].std(axis=1)

    # Statistics of the whole previous day (constant over the forecast day)
    day = idx.normalize()
    daily_mean = series.groupby(day).mean()
    daily_max = series.groupby(day).max()
    prev_day = day - pd.Timedelta(days=1)
    X["prev_day_mean"] = daily_mean.reindex(prev_day).values
    X["prev_day_max"] = daily_max.reindex(prev_day).values

    # Upper envelope of the same quarter-hour over the previous 14 days
    # (a clear-sky proxy for PV, a recent peak level for load).
    X["same_qh_max_14d"] = pd.concat([series.shift(d * STEPS_PER_DAY) for d in range(1, 15)],
                                     axis=1).max(axis=1)

    return X


def split(X, y):
    valid = X.notna().all(axis=1)
    X, y = X[valid], y[valid]
    tr = X.index <= TRAIN_END
    va = (X.index > TRAIN_END) & (X.index <= VAL_END)
    te = X.index > VAL_END
    return (X[tr], y[tr]), (X[va], y[va]), (X[te], y[te])


# =============================================================================
# MODEL
# =============================================================================

def fit_xgb(train, val, seed=0):
    (X_tr, y_tr), (X_va, y_va) = train, val
    model = xgb.XGBRegressor(
        n_estimators=600,
        learning_rate=0.08,
        max_depth=6,
        min_child_weight=5,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        objective="reg:squarederror",
        early_stopping_rounds=50,
        tree_method="hist",
        random_state=seed,
        n_jobs=-1,
    )
    model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
    best_n = model.best_iteration + 1

    # Refit on train + validation with the selected number of trees.
    final = xgb.XGBRegressor(**{**model.get_params(), "n_estimators": best_n,
                                "early_stopping_rounds": None})
    final.fit(pd.concat([X_tr, X_va]), pd.concat([y_tr, y_va]), verbose=False)
    return final, best_n


def metrics(y_true, y_pred, mask=None):
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    if mask is not None:
        y_true, y_pred = y_true[mask], y_pred[mask]
    err = y_pred - y_true
    mae = np.mean(np.abs(err))
    rmse = np.sqrt(np.mean(err ** 2))
    return {"MAE": mae, "RMSE": rmse, "nMAE_%": 100 * mae / np.mean(y_true)}


# =============================================================================
# FIGURE
# =============================================================================

OKABE = {"black": "#000000", "orange": "#E69F00", "blue": "#0072B2",
         "green": "#009E73", "vermillion": "#D55E00", "grey": "#999999"}


def _journal_style():
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": 8,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.linewidth": 0.8, "axes.grid": False,
        "xtick.direction": "out", "ytick.direction": "out",
        "xtick.major.width": 0.8, "ytick.major.width": 0.8,
        "axes.labelsize": 9, "legend.fontsize": 7, "legend.frameon": False,
    })


def plot_test_week(fc, start="2021-11-08", days=7):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    _journal_style()
    w = fc.loc[start: pd.Timestamp(start) + pd.Timedelta(days=days) - pd.Timedelta(minutes=15)]
    fig, axes = plt.subplots(2, 1, figsize=(7.0, 4.6), sharex=True, constrained_layout=True)

    for ax, var, ylab, lab in [(axes[0], "load", "Household load (kW)", "A"),
                               (axes[1], "pv", "PV output (kW kWp$^{-1}$)", "B")]:
        ax.plot(w.index, w[f"{var}_actual"], color=OKABE["black"], lw=1.0, label="Measured")
        ax.plot(w.index, w[f"{var}_xgb"], color=OKABE["blue"], lw=1.0, ls="-",
                label="XGBoost day-ahead")
        ax.plot(w.index, w[f"{var}_persist"], color=OKABE["orange"], lw=1.0, ls="--",
                label="Persistence (previous day)")
        ax.set_ylabel(ylab)
        ax.text(-0.08, 1.02, lab, transform=ax.transAxes, fontsize=11, fontweight="bold",
                va="bottom", ha="left")
    axes[0].legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=3)
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    axes[1].set_xlabel("Date (2021)")

    os.makedirs(FIG_DIR, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(FIG_DIR, f"forecast_test_week.{ext}"), dpi=300)
    plt.close(fig)


# =============================================================================
# MAIN
# =============================================================================

def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    prof = load_profiles()

    fc = pd.DataFrame(index=prof.index[prof.index > VAL_END])
    rows = []

    for var, col in [("load", "load_kw"), ("pv", "pv_pu")]:
        y = prof[col]
        X = make_features(y)
        if var == "pv":
            # Learn the PV output as a fraction of its recent clear-sky envelope,
            # which removes most of the seasonal level the model cannot extrapolate.
            env = X["same_qh_max_14d"]
            target = (y / env.where(env > 1e-3)).fillna(0.0).clip(0, 1.5)
        else:
            env, target = None, y
        train, val, test = split(X, target)
        model, best_n = fit_xgb(train, val)

        X_te = test[0]
        y_te = y.loc[X_te.index]
        pred = np.clip(model.predict(X_te), 0, None)
        if var == "pv":
            pred = pred * X_te["same_qh_max_14d"].values
        persist = X_te["lag_1d"].values
        profile7 = X_te["same_qh_mean_7d"].values

        fc[f"{var}_actual"] = y_te.values
        fc[f"{var}_xgb"] = pred
        fc[f"{var}_persist"] = persist
        fc[f"{var}_profile7"] = profile7

        # PV errors are also reported over daylight steps only.
        mask = y_te.values > 1e-3 if var == "pv" else None
        for name, p in [("XGBoost", pred), ("Persistence", persist), ("7-day profile", profile7)]:
            m = metrics(y_te, p)
            row = {"target": var, "model": name, **m}
            if mask is not None:
                row["MAE_daylight"] = metrics(y_te, p, mask)["MAE"]
            rows.append(row)
        print(f"{var}: XGBoost trees selected on validation = {best_n}")

    met = pd.DataFrame(rows)
    met.to_csv(os.path.join(RESULTS_DIR, "part3_forecast_metrics.csv"), index=False,
               float_format="%.4f")
    fc.to_csv(os.path.join(RESULTS_DIR, "part3_forecasts_test.csv"), float_format="%.6f")

    print("\nTest-set accuracy (1 Oct - 31 Dec 2021, day-ahead):")
    print(met.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    plot_test_week(fc)
    print(f"\nSaved forecasts, metrics and figure to {RESULTS_DIR} and {FIG_DIR}")


if __name__ == "__main__":
    main()
