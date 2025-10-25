# Etapa 1: build leve (opcional se precisar de wheels nativos)
FROM python:3.11-slim AS base

# Evita prompt interativo e melhora confiabilidade de rede
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# App
WORKDIR /app
# Caso tenha requirements.txt, mantenha-o. Senão, gere a partir do teu poetry/pip.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . /app

# Exponha a porta **interna** onde o Uvicorn vai escutar (não precisa ser pública)
ENV PORT=8000
# Permite ativar/desativar o bot do Teams via env
ENV ENABLE_TEAMS_BOT=1

# Comando de arrancada: workers 2 (ajuste conforme CPU); timeout generoso p/ APIs externas
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
