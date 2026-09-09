FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./

# Stub app package so editable install can resolve it and cache deps in this layer.
# Real source overwrites the stub in the COPY below.
RUN mkdir -p app && touch app/__init__.py && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
