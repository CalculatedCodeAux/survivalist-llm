FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cache-friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY templates/ templates/

# Create directories that will be bind-mounted as volumes
# (prevents permission errors if host dirs don't exist yet)
RUN mkdir -p /packs /state

EXPOSE 5000

# gunicorn: 1 worker (single-request concurrency is fine for admin UI),
# 120s timeout for large ZIM uploads processed synchronously
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "app:app"]
