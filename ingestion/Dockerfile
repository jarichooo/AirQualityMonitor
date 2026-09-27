FROM python:3.9-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the ingestion script
COPY ingest.py .

# Run Python in unbuffered mode so print() logs show up immediately in Docker Desktop
CMD ["python", "-u", "ingest.py"]