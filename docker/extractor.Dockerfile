# The screener itself: no web server, no exposed port. It screens the market on
# a schedule and writes JSON to the shared volume, and that is all it does.
FROM python:3.11-slim

# Runs unprivileged. /data is created here, owned by this user, so the named
# volume Compose mounts over it inherits that ownership -- a volume created
# against a root-owned path is the usual reason a non-root container cannot
# write to its own data directory.
RUN useradd --create-home --uid 10001 screener \
    && mkdir -p /data && chown screener:screener /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY warrior_screener/ warrior_screener/
COPY config/ config/

ENV PYTHONUNBUFFERED=1 \
    SCREENER_DATA_DIR=/data

USER screener
CMD ["python", "-m", "warrior_screener.scheduler"]
