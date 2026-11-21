FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pb_mosaic ./pb_mosaic

# 与框幅 worker 相同的 uid：客户端 start.sh 按 10001 给 data 目录授权
RUN useradd -m -u 10001 mosaic \
    && mkdir -p /data/.license /data/.pushbroom-worker \
    && chown -R mosaic:mosaic /app /data
USER mosaic

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import os,sys,time; d=os.environ.get('PUSHBROOM_WORKER_STATE_DIR','/data/.pushbroom-worker'); sys.exit(0 if time.time()-os.path.getmtime(d+'/status.json')<60 else 1)"

CMD ["python", "-m", "pb_mosaic.worker_main"]
