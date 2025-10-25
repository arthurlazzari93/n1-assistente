# Python 3.11 slim + build deps mínimos
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# deps do sistema (ajuste se o bot/graph precisar de algo extra)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements primeiro (melhora cache)
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# código
COPY . /app

# usuário não-root (para escrever app.log criado pelo seu main)
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

# health: seu app expõe GET /healthz (ok)
# comando: uvicorn rodando app.main:app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
