FROM python:3.11-slim

WORKDIR /app

# Install Poetry
RUN pip install poetry

# Copy pyproject.toml and poetry.lock and README.md (all needed for poetry install)
COPY pyproject.toml poetry.lock* README.md /app/

# Install dependencies
RUN poetry install --no-root

# Copy the rest of the application code
COPY src /app/src
COPY config /app/config
# Add any other top-level files here, e.g., .env.example if needed

# Set environment variables (optional)
ENV PYTHONUNBUFFERED=1

# No default CMD; user must specify the command when running the container