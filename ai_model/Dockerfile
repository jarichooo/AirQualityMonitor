FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --only-binary=:all: -r /tmp/requirements.txt && pip check

RUN mkdir -p "/app/ai_model/reasoning ai"
COPY __init__.py forecast_worker.py policy.py predictor.py predict.py schema.sql microclimate_forecaster.joblib /app/ai_model/
COPY simulation.py simulate_good.py simulate_mid.py simulate_bad.py simulate_series.py /app/ai_model/
COPY ["reasoning ai/thresholds.md", "/app/ai_model/reasoning ai/thresholds.md"]
RUN python -c "from ai_model.predictor import _load_forecaster; _load_forecaster()"

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "ai_model.forecast_worker"]
