# Use official Python 3.11 slim image
FROM python:3.11-slim

# Install system dependencies: ffmpeg, curl, gcc (for some compiled wheels), libglib2.0-0
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        gcc \
        libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# ── Step 1: CPU-only PyTorch ───────────────────────────────────────────────────
# Install from the official PyTorch CPU wheel index BEFORE the rest of requirements.txt
# so that openai-whisper (and pip) find a pre-installed CPU torch and do NOT
# pull the full CUDA / cuDNN bundles (~500 MB) from PyPI.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.2.2

# ── Step 2: Remaining application dependencies ────────────────────────────────
# torch is already satisfied above; pip will skip re-installing it.
# openai-whisper resolves its torch requirement against the installed CPU wheel.
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
