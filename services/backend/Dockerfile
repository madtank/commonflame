FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
RUN groupadd --gid 10001 commonflame && useradd --uid 10001 --gid commonflame --create-home commonflame \
    && mkdir -p /run/keys /app/uploads && chown -R commonflame:commonflame /run/keys /app/uploads
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1 setuptools==84.0.0 \
    && python -m pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY api ./api
COPY alembic ./alembic
COPY scripts ./scripts
COPY alembic.ini auth.md prometheus_monitoring.py LICENSE NOTICE ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh
USER commonflame
EXPOSE 8080
ENTRYPOINT ["/app/docker-entrypoint.sh"]
