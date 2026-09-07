# Build context is the repo root (D:\sih), referenced from a root
# docker-compose.yml, since this service imports services/common at runtime.
#
#   docker build -f services/optimization-service/Dockerfile -t qfleet-optimization-service .
#
FROM python:3.12-slim

WORKDIR /srv

COPY services/optimization-service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/optimization-service/app ./app
COPY services/common ./common

# data-service's FeaturePipeline is needed at runtime (VoyageRequest ->
# ProcessedFeatures, before calling prediction-service over RPC), but
# both services name their own package `app`, so it can't be copied to
# ./app alongside this service's own package -- copied under a distinct
# name instead. app/optimizer.py's cross-service-import shim looks for it
# at ./data_service_app (see that file's Docker-vs-local-dev path
# resolution), loading it via importlib under a distinct sys.modules key
# rather than a plain import, exactly like prediction-service/train.py's
# equivalent shim.
COPY services/data-service/app ./data_service_app

# Mirrors conftest.py's sys.path setup: `common` and `app` both resolve from
# /srv at runtime the same way they resolve from services/optimization-service in tests.
ENV PYTHONPATH=/srv

EXPOSE 8003

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
