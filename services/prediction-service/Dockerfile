# Build context is the repo root (D:\sih), referenced from a root
# docker-compose.yml, since this service imports services/common at runtime.
#
#   docker build -f services/prediction-service/Dockerfile -t qfleet-prediction-service .
#
FROM python:3.12-slim

WORKDIR /srv

COPY services/prediction-service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/prediction-service/app ./app
COPY services/common ./common
COPY data/models ./data/models

# Mirrors conftest.py's sys.path setup: `common` and `app` both resolve from
# /srv at runtime the same way they resolve from services/prediction-service in tests.
ENV PYTHONPATH=/srv

EXPOSE 8001

# Runs both the REST API (uvicorn) and the RabbitMQ RPC server
# (app/rpc_server.py's start_rpc_server(), consumed by optimization-service)
# as background processes in the same container, so `wait` keeps the
# container alive as long as either is running. This is a pragmatic
# simplification for this stage of the project -- a production setup would
# run these as separate containers/processes under a real process
# supervisor (e.g. separate Compose services, or supervisord) instead of
# a shell background job.
CMD ["bash", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8001 & python -m app.rpc_server & wait -n"]
