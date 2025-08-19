# ---------- Builder: install Python deps and build wheels ----------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System packages needed to build some wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only dependency files first for better caching
# If you don't have requirements.txt yet, create one with your libs.
COPY requirements.txt .

RUN pip wheel --wheel-dir=/wheels -r requirements.txt


# ---------- Final runtime image ----------
FROM python:3.11-slim

# Minimal runtime libs for Streamlit + PyMuPDF (fitz) + healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Streamlit
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ENABLECORS=false \
    STREAMLIT_SERVER_ENABLEXsrfProtection=false

# Create a non-root user
RUN useradd -ms /bin/bash appuser
WORKDIR /app

# Copy wheels from builder and install
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

# Copy the application code
# Adjust if your entry file is not app.py
COPY . .

# Optional: Streamlit config (overridden by env vars above)
RUN mkdir -p /home/appuser/.streamlit && \
    printf "[server]\nheadless = true\nenableCORS = false\nenableXsrfProtection = false\n" \
    > /home/appuser/.streamlit/config.toml && \
    chown -R appuser:appuser /home/appuser /app

USER appuser

EXPOSE 8501

# Basic healthcheck on Streamlit HTTP endpoint
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -f http://127.0.0.1:8501/_stcore/health || exit 1

# If your main file is different, change app.py below
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
    