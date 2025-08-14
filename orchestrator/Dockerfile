FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir flask pyyaml

# Copy application code
COPY . /app/

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV REPO_ROOT=/workspace

# Expose port
EXPOSE 8000

# Run the application
CMD ["python", "-m", "orchestrator"]
