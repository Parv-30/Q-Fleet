# Build context is the repo root (D:\sih), referenced from a root
# docker-compose.yml. Unlike optimization-service, the gateway makes no
# cross-service Python imports (services/common is not needed) -- it only
# ever talks to the other services over HTTP -- so this Dockerfile is
# simpler, closer to emissions-service's pattern minus the `common` copy.
#
#   docker build -f services/gateway/Dockerfile -t qfleet-gateway .
#
FROM python:3.12-slim

WORKDIR /srv

COPY services/gateway/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/gateway/app ./app

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
