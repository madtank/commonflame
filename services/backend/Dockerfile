FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
RUN groupadd --gid 10001 waystation && useradd --uid 10001 --gid waystation --create-home waystation \
    && mkdir -p /run/keys /app/uploads && chown -R waystation:waystation /run/keys /app/uploads
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1 setuptools==84.0.0 \
    && python -m pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY api ./api
COPY alembic ./alembic
COPY scripts ./scripts
COPY alembic.ini auth.md prometheus_monitoring.py ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh
USER waystation
EXPOSE 8080
ENTRYPOINT ["/app/docker-entrypoint.sh"]
