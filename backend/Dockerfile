# Use official Python 3.11 slim image
FROM python:3.11-slim

# Install system dependencies: ffmpeg, curl, build-essential (for torch compiled wheels), and any needed libs
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        gcc \
        libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy only requirements first for caching
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy the entire backend source code
COPY backend ./backend

# Ensure the Python path includes /app so imports like backend.app.main work
ENV PYTHONPATH=/app

# Create a volume for Whisper model cache (optional, can be overridden by compose)
VOLUME ["/root/.cache/whisper"]

# Expose the FastAPI port
EXPOSE 8000

# Default command to run the app
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
