# Commonflame MCP resource server
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 10001 --create-home app
WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1 setuptools==84.0.0 \
    && python -m pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY fastmcp_server/ ./fastmcp_server/
COPY LICENSE NOTICE ./

USER app
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s CMD curl -fsS http://localhost:8080/health || exit 1

ENV FASTMCP_HOST=0.0.0.0
ENV MCP_UVICORN_WORKERS=2

# Run stateless containers with multiple Uvicorn workers so
# /health probes are not starved behind one busy MCP request on a single event
# loop. Stateful Streamable HTTP sessions are in-process, so force one worker
# for any falsy MCP_STATELESS_HTTP value (false/False/FALSE/0/no/off) to avoid
# intermittent session misses — a case-sensitive "= false" check would silently
# run multiple workers for a value like "False".
# Disable access logs because legacy OAuth/SSE URLs can carry credentials in
# query strings. Application diagnostics and authentication failures still log.
CMD ["sh", "-c", "case \"$(printf '%s' \"${MCP_STATELESS_HTTP:-true}\" | tr '[:upper:]' '[:lower:]')\" in 0|false|no|off) WORKERS=1 ;; *) WORKERS=${MCP_UVICORN_WORKERS:-2} ;; esac; exec uvicorn fastmcp_server.server:create_app --factory --host ${FASTMCP_HOST:-0.0.0.0} --port ${FASTMCP_PORT:-${PORT:-8080}} --workers $WORKERS --no-access-log"]
