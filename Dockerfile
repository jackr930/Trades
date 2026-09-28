# --- build the web UI --------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- runtime -----------------------------------------------------------------------
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRADES_HOME=/data \
    TRADES_WEB_DIST=/app/web/dist
COPY pyproject.toml README.md ./
COPY trades ./trades
RUN pip install --no-cache-dir .
COPY --from=web /web/dist ./web/dist
VOLUME ["/data"]
EXPOSE 8000
# Inside the container the server must listen on all interfaces; docker-compose publishes
# the port on 127.0.0.1 only, so the app is still reachable from this machine alone.
CMD ["trades", "serve", "--host", "0.0.0.0", "--port", "8000"]
