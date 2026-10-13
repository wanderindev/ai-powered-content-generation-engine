FROM python:3.12-slim

WORKDIR /app

# Shared, world-readable browser cache so the chromium installed below (as root)
# is reachable by the non-root appuser at runtime (#321).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    fonts-dejavu-core \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Headless chromium for slide rendering (#321). --with-deps pulls the browser's
# system libraries; the binary lands in PLAYWRIGHT_BROWSERS_PATH (world-readable).
RUN playwright install --with-deps chromium && chmod -R a+rx /ms-playwright

# Copy application code
COPY . .

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]