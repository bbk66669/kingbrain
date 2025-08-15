FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir flask pyyaml

# Copy application code
COPY . /app/

# Set environment variables
ENV PYTHONUNBUFFERED=1
# repo root must be identical to the hostPath mount so that written
# files are visible on the node
ENV REPO_ROOT=/srv/kingbrain

# Expose port
EXPOSE 8000

# Run the application
CMD ["python", "-m", "orchestrator"]
