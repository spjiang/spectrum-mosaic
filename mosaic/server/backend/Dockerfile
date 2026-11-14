FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
# psycopg[binary] 自带 libpq，不必再装系统库。Debian 镜像里的 apt 清理钩子会让 apt-get update 失败。
RUN pip install --no-cache-dir --progress-bar off -r requirements.txt
COPY app ./app
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
