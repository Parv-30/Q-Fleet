# Build context is the repo root (D:\sih), referenced from a root
# docker-compose.yml, since this service imports services/common at runtime.
#
#   docker build -f services/data-service/Dockerfile -t qfleet-data-service .
#
FROM python:3.12-slim

WORKDIR /srv

COPY services/data-service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/data-service/app ./app
COPY services/common ./common

# Mirrors conftest.py's sys.path setup: `common` and `app` both resolve from
# /srv at runtime the same way they resolve from services/data-service in tests.
ENV PYTHONPATH=/srv

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
