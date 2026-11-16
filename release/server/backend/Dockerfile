FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && ARCH="$(uname -m)" \
    && case "$ARCH" in \
         x86_64) DARCH=x86_64; BX=linux-amd64 ;; \
         aarch64|arm64) DARCH=aarch64; BX=linux-arm64 ;; \
         *) echo "unsupported arch $ARCH" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DARCH}/docker-27.5.1.tgz" \
         | tar -xz -C /usr/local/bin --strip-components=1 docker/docker \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/buildx/releases/download/v0.20.1/buildx-v0.20.1.${BX}" \
         -o /usr/local/lib/docker/cli-plugins/docker-buildx \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/* \
    && docker --version \
    && docker buildx version
COPY app ./app
ENV PYTHONUNBUFFERED=1
ENV DOCKER_CLI_PLUGIN_PATH=/usr/local/lib/docker/cli-plugins
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
