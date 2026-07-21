# Windstar Tahiti Price Tracker — cloud image (Playwright + FastAPI)
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8765 \
    DISABLE_LOCAL_SCHEDULER=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY static ./static
COPY scripts ./scripts
COPY run.py .

# Persistent volume should mount at /app/data in production
RUN mkdir -p /app/data

EXPOSE 8765

# Web dashboard + secured /api/check-prices for external cron
CMD ["python", "run.py"]
