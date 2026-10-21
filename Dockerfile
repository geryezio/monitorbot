FROM python:3.15-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

ENV PSUTIL_PROC_FS_PATH=/host_proc
ENV DOCKER_HOST=unix:///var/run/docker.sock
ENV DOCKER_CLI_PLUGINS_ORIGIN_DIR=/usr/local/libexec/docker/cli-plugins
ENV PYTHONUNBUFFERED=1
CMD ["python", "-u", "bot.py"]
