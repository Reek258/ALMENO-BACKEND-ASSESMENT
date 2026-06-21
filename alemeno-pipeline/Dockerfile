FROM python:3.11-slim

WORKDIR /code

# System deps for psycopg2 build (binary wheel normally avoids this, kept for safety)
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Directory where uploaded CSVs are persisted (mounted as a volume in compose)
RUN mkdir -p /code/uploads

EXPOSE 8000

# Default command runs the API; the worker service overrides this in docker-compose.yml
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
