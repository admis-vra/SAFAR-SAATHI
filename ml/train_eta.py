"""Train the departure-delay model and keep it only if it beats the baseline.

Data: trips.csv with columns weekday,hour,exam_day,load,delay_min
  - If trips.csv is missing, a SYNTHETIC file is generated so the pipeline runs.
    Synthetic results are not evidence; replace with real driver-app logs before claiming accuracy.
"""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
from xgboost import XGBRegressor

HERE = Path(__file__).parent
DATA = HERE / "trips.csv"
OUT = HERE / "artifacts"
FEATURES = ["weekday", "hour", "exam_day", "load"]


def make_synthetic(n=1500, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "weekday": rng.integers(0, 6, n),
        "hour": rng.choice([8, 9, 13, 14, 16, 17], n),
        "exam_day": rng.random(n) < 0.2,
        "load": rng.uniform(0.2, 1.0, n).round(2),
    })
    delay = (
        2
        + 4 * (df["hour"].isin([13, 17]))
        + 3 * df["exam_day"]
        + 6 * (df["load"] - 0.5).clip(lower=0)
        + 1.5 * (df["weekday"] == 0)
        + rng.normal(0, 1.5, n)
    )
    df["delay_min"] = delay.clip(lower=0).round(1)
    df["exam_day"] = df["exam_day"].astype(int)
    return df


def main():
    synthetic = not DATA.exists()
    df = make_synthetic() if synthetic else pd.read_csv(DATA)
    if synthetic:
        df.to_csv(HERE / "trips_synthetic.csv", index=False)
        print("WARNING: trips.csv not found, trained on SYNTHETIC data (trips_synthetic.csv)")

    train, test = train_test_split(df, test_size=0.25, random_state=0)
    baseline = float(train["delay_min"].mean())
    base_mae = mean_absolute_error(test["delay_min"], np.full(len(test), baseline))

    model = XGBRegressor(n_estimators=200, max_depth=3, learning_rate=0.1)
    model.fit(train[FEATURES].values, train["delay_min"].values)
    model_mae = mean_absolute_error(test["delay_min"], model.predict(test[FEATURES].values))

    use_model = model_mae < base_mae
    OUT.mkdir(exist_ok=True)
    joblib.dump(model, OUT / "eta_model.joblib")
    meta = {"baseline_delay_min": round(baseline, 2), "baseline_mae": round(base_mae, 2),
            "model_mae": round(model_mae, 2), "use_model": use_model, "synthetic_data": synthetic,
            "rows": len(df)}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    print("Using model" if use_model else "Model did not beat baseline; backend will use the baseline")


if __name__ == "__main__":
    main()
