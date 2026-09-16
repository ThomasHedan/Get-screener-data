FROM python:3.12-slim

# Unprivileged: this service opens no ports and needs nothing but outbound HTTPS.
RUN useradd --create-home --uid 10001 screener
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY warrior_screener/ warrior_screener/
COPY config/ config/

ENV PYTHONUNBUFFERED=1
USER screener
CMD ["python", "-m", "warrior_screener.scraper"]
