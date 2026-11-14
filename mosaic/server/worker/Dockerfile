FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libglib2.0-0 \
        g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-worker.txt .
RUN pip install --no-cache-dir -r requirements-worker.txt \
    && apt-get purge -y --auto-remove g++ \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY ms_mosaic ./ms_mosaic

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    MPLBACKEND=Agg

CMD ["python", "-m", "ms_mosaic.worker_main"]
