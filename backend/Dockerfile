FROM python:3.12-slim

WORKDIR /workspace
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/app backend/app
COPY backend/migrations backend/migrations
COPY backend/migrate.py backend/migrate.py
COPY screener/pipeline screener/pipeline
COPY screener/config.yaml screener/config.yaml

WORKDIR /workspace/backend
ENV PYTHONPATH=/workspace/backend:/workspace

EXPOSE 8081
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
