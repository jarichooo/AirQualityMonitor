"""Load the exported GRU and predict the next 120 minutes; no training code."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


def sigmoid(values):
    # Extreme demo concentrations saturate gates without overflowing exp.
    return np.exp(-np.logaddexp(0, -values))


def numpy_gru(features, weights):
    """Evaluate the exported two-layer GRU equations in float32 on CPU.

    PyTorch's gate order is reset/update/new. Dropout is disabled for inference.
    This is the same trained network, without requiring a GPU runtime download.
    """
    sequence = np.asarray(features[0], dtype=np.float32)
    latest = sequence[-1, :8].copy()
    for layer in range(2):
        prefix = f"gru."
        wi, wh = weights[f"{prefix}weight_ih_l{layer}"], weights[f"{prefix}weight_hh_l{layer}"]
        bi, bh = weights[f"{prefix}bias_ih_l{layer}"], weights[f"{prefix}bias_hh_l{layer}"]
        input_gates = sequence @ wi.T + bi
        hidden = np.zeros(64, dtype=np.float32)
        output = np.empty((len(sequence), 64), dtype=np.float32)
        for t, gates in enumerate(input_gates):
            recurrent = wh @ hidden + bh
            reset = sigmoid(gates[:64] + recurrent[:64])
            update = sigmoid(gates[64:128] + recurrent[64:128])
            new = np.tanh(gates[128:] + reset * recurrent[128:])
            hidden = (1-update)*new + update*hidden
            output[t] = hidden
        sequence = output
    head = np.maximum(hidden @ weights["head.0.weight"].T + weights["head.0.bias"], 0)
    forecast = head @ weights["head.2.weight"].T + weights["head.2.bias"]
    return forecast.reshape(1, 120, 8) + latest[None, None]


def torch_gru():
    # Import lazily: CPU inference does not depend on the PyTorch installation.
    import torch
    from torch import nn

    class GRUForecaster(nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = nn.GRU(10, 64, num_layers=2, dropout=.1, batch_first=True)
            self.head = nn.Sequential(nn.Linear(64, 128), nn.ReLU(), nn.Linear(128, 120 * 8))

        def forward(self, x):
            _, state = self.gru(x)
            return self.head(state[-1]).reshape(-1, 120, 8) + x[:, -1, None, :8]

    return GRUForecaster()


def project(values):
    values = np.array(values, dtype=np.float32, copy=True)
    values[..., 1] = np.clip(values[..., 1], 0, 100)
    values[..., 2:] = np.maximum(values[..., 2:], 0)
    values[..., 3:6] = np.maximum.accumulate(values[..., 3:6], axis=-1)
    return values


def load_forecaster(model_path=None, device="auto"):
    """Load this package's trusted Joblib artifact onto CPU or CUDA."""
    path = Path(model_path) if model_path else Path(__file__).with_name("microclimate_forecaster.joblib")
    bundle = joblib.load(path)
    if (bundle.get("format_version") != 1 or bundle.get("model_type") != "GRU"
            or bundle.get("history") != 120 or bundle.get("horizon") != 120
            or len(bundle.get("sensors", [])) != 8):
        raise ValueError("This client loader supports the supplied version-1 GRU artifact only.")
    if device not in ("auto", "cpu", "cuda"):
        raise ValueError("device must be auto, cpu or cuda.")
    mean, std = np.asarray(bundle["mean"]), np.asarray(bundle["std"])
    if mean.shape != (8,) or std.shape != (8,) or not np.isfinite(mean).all() or not np.isfinite(std).all() or (std <= 0).any():
        raise ValueError("Invalid normalization parameters.")
    if device == "cpu":
        return {"bundle": bundle, "model": None, "device": "cpu", "backend": "numpy"}
    try:
        import torch
    except ImportError:
        if device == "cuda":
            raise RuntimeError("Install CUDA-enabled PyTorch or use --device cpu.") from None
        return {"bundle": bundle, "model": None, "device": "cpu", "backend": "numpy"}
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        return {"bundle": bundle, "model": None, "device": "cpu", "backend": "numpy"}
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable. Use --device cpu or install CUDA-enabled PyTorch.")
    model = torch_gru()
    model.load_state_dict({k: torch.from_numpy(v.copy()) for k, v in bundle["state_dict"].items()})
    model = model.to(device).eval()
    return {"bundle": bundle, "model": model, "device": torch.device(device), "backend": "torch"}


def forecast_next_120_minutes(recent_readings, forecaster):
    """Return a 120×8 DataFrame indexed by future UTC minutes.

    Input: exactly 120 consecutive, minute-aligned observations, a timezone-aware
    DatetimeIndex, and the artifact's sensor columns in their original order.
    """
    bundle = forecaster["bundle"]
    if not isinstance(recent_readings, pd.DataFrame) or list(recent_readings.columns) != bundle["sensors"]:
        raise ValueError("Expected the eight original sensor columns, in model order.")
    if len(recent_readings) != 120:
        raise ValueError("Exactly 120 readings are required.")
    if not isinstance(recent_readings.index, pd.DatetimeIndex) or recent_readings.index.tz is None:
        raise ValueError("Use a timezone-aware DatetimeIndex.")
    index = recent_readings.index.tz_convert("UTC")
    if not index.is_monotonic_increasing or index.has_duplicates or not index.equals(index.floor("min")):
        raise ValueError("Timestamps must be ascending, unique, and minute-aligned.")
    if not np.all(index[1:] - index[:-1] == pd.Timedelta(minutes=1)):
        raise ValueError("Input minutes must be consecutive; no implicit gap filling is performed.")
    values = recent_readings.to_numpy(dtype=np.float32)
    if not np.isfinite(values).all():
        raise ValueError("Input values must be finite.")
    if ((values[:, 1] < 0).any() or (values[:, 1] > 100).any()
            or (values[:, 2:] < 0).any() or (values[:, 3] > values[:, 4]).any()
            or (values[:, 4] > values[:, 5]).any()):
        raise ValueError("Input violates humidity, concentration or particulate-order constraints.")
    normalized = (values - bundle["mean"]) / bundle["std"]
    phase = 2 * np.pi * (index.hour * 60 + index.minute) / 1440
    features = np.column_stack([normalized, np.sin(phase), np.cos(phase)]).astype(np.float32)[None]
    if forecaster["backend"] == "numpy":
        predicted = numpy_gru(features, bundle["state_dict"])
    else:
        import torch
        with torch.inference_mode():
            predicted = forecaster["model"](torch.from_numpy(features).to(forecaster["device"])).cpu().numpy()
    predicted = project(predicted * bundle["std"] + bundle["mean"])[0]
    if not np.isfinite(predicted).all():
        raise FloatingPointError("Model produced nonfinite predictions.")
    future = pd.date_range(index[-1] + pd.Timedelta(minutes=1), periods=120,
                           freq="min", name="Timestamp (UTC)")
    return pd.DataFrame(predicted, index=future, columns=bundle["sensors"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("prediction.csv"))
    parser.add_argument("--model", type=Path, default=Path(__file__).with_name("microclimate_forecaster.joblib"))
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    args = parser.parse_args()
    if args.output.resolve() in (args.input.resolve(), args.model.resolve()):
        raise ValueError("Output must not overwrite the input CSV or model artifact.")
    if args.device != "cpu":
        try:
            import torch
        except ImportError:
            pass
        else:
            torch.set_num_threads(min(4, os.cpu_count() or 1))
    frame = pd.read_csv(args.input)
    if frame.columns[0] != "Timestamp (UTC)":
        raise ValueError("First CSV column must be Timestamp (UTC).")
    index = pd.to_datetime(frame.iloc[:, 0], utc=True, errors="raise")
    recent = frame.iloc[:, 1:].set_axis(index)
    forecaster = load_forecaster(args.model, args.device)
    prediction = forecast_next_120_minutes(recent, forecaster)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    prediction.to_csv(args.output, index_label="Timestamp (UTC)")
    print(f"GRU inference on {forecaster['device']}: {len(prediction)} forecast minutes saved to {args.output}")
    print(prediction.iloc[[0, 29, 59, 89, 119]].to_string())


if __name__ == "__main__":
    main()
