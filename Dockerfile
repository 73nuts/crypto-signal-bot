# Crypto Futures Signal System - Multi-Currency Support
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Shanghai

# Install system dependencies (compiler tools for bip_utils, fonts for image generation)
RUN apt-get update && apt-get install -y \
    tzdata \
    curl \
    gcc \
    g++ \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Set timezone
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copy dependency file and install Python packages (locked versions for reproducible builds)
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# Copy config files
COPY config_futures.yaml .
COPY config/ ./config/

# Copy source modules
COPY src/ ./src/

# Copy utility scripts
COPY scripts/ ./scripts/

# Copy test directory
COPY tests/ ./tests/

# Create required directories
RUN mkdir -p logs

# Health check
HEALTHCHECK --interval=30m --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import ccxt; print('OK')" || exit 1

# Expose ports (Webhook + Dashboard)
EXPOSE 8080 5000

# Default: run Swing scheduler
CMD ["python", "-m", "src.strategies.swing.scheduler"]