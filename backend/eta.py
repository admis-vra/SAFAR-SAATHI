import json
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent.parent / "ml" / "artifacts"
_model = None
_baseline = None


def _load():
    global _model, _baseline
    meta_path = MODEL_DIR / "meta.json"
    if not meta_path.exists():
        return
    meta = json.loads(meta_path.read_text())
    _baseline = meta["baseline_delay_min"]
    # Model is only used when it beat the baseline on held-out data during training.
    if meta.get("use_model"):
        try:
            import joblib
            _model = joblib.load(MODEL_DIR / "eta_model.joblib")
        except Exception:
            _model = None


_load()


def predict_delay_minutes(weekday: int, hour: int, exam_day: bool, load: float) -> tuple[float, str]:
    if _model is not None:
        pred = float(_model.predict([[weekday, hour, int(exam_day), load]])[0])
        return pred, "model"
    if _baseline is not None:
        return _baseline, "baseline"
    return 0.0, "timetable"
