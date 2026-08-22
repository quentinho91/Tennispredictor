FROM python:3.11-slim

WORKDIR /app

# Dépendances système
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copie et installation des requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code source et des données précalculées
COPY . .

# Port standard Hugging Face Spaces & Render
EXPOSE 7860

# Lancement du serveur FastAPI (lit $PORT de manière dynamique)
CMD ["python", "app.py"]

