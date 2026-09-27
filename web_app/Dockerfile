FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

# Runs Flask on port 5000, bound to all network interfaces
CMD ["python", "app.py"]