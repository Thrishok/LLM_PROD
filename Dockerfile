FROM python:3.13-slim

WORKDIR /app

# Install dependencies first so this layer is cached when only code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY main.py .
COPY auth.py .
COPY db.py .
COPY static/ ./static/

EXPOSE 8000

# Bind to 0.0.0.0 so the server is reachable from outside the container.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
