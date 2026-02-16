FROM python:3.11-slim

WORKDIR /app

# Install Poetry
RUN pip install poetry

# Copy only dependency files first for better build caching
COPY pyproject.toml poetry.lock* /app/

# Install dependencies (no dev dependencies for production)
RUN poetry install --no-root

# Copy the rest of the code
COPY . /app

# Set environment variables (optional)
ENV PYTHONUNBUFFERED=1

# No default CMD; user must specify the command when running the container
