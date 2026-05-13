FROM python:3.13-slim

WORKDIR /app

# System deps for vnpy CTP (gcc for potential C extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir slowapi

COPY . .

ENV QUANT_ENV=production
ENV QUANT_DB_PATH=/app/data/historical/quotes.db

RUN mkdir -p /app/data/historical

EXPOSE 8000

CMD ["uvicorn", "src.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
