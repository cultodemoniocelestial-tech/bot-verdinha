# Usar imagem base do Python
FROM python:3.11-slim

# Definir diretório de trabalho
WORKDIR /app

# Instalar dependências do sistema necessárias para o Playwright/Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    fonts-liberation \
    fonts-noto-color-emoji \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar arquivos de dependências primeiro para otimizar o cache do Docker
COPY requirements.txt .

# Instalar dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Instalar navegador Chromium via Playwright
RUN playwright install chromium

# Copiar o resto do código da aplicação
COPY . .

# Criar diretório de downloads
RUN mkdir -p /app/downloads

# Expor a porta da aplicação
EXPOSE 5000

# Healthcheck para verificar se a aplicação está funcionando
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# Definir comando para rodar a aplicação
CMD ["python", "app.py"]
