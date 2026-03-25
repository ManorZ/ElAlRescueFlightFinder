FROM python:3.11-slim

# Playwright Chromium system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates fonts-liberation libasound2 libatk-bridge2.0-0 \
    libatk1.0-0 libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 \
    libnspr4 libnss3 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libxshmfence1 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# Install Playwright Chromium browser binary
RUN playwright install chromium

COPY . .
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV FLASK_HOST=0.0.0.0
ENV FLASK_PORT=5000
ENV HEADLESS=1

EXPOSE 5000
CMD ["python", "app.py"]
