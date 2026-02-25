# ------------------------------------------------------------------
# Dockerfile for the agent-api service
# Builds a lightweight Python 3.12 image that runs the FastAPI chat server.
# ------------------------------------------------------------------

# Start from the official slim Python image to keep the image small
FROM python:3.12-slim

# Set the working directory inside the container
WORKDIR /app

# Copy and install Python dependencies first (leverages Docker layer caching —
# dependencies are only reinstalled when requirements.txt changes)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source code into the container
COPY src ./src

# Declare the port the server will listen on
EXPOSE 9000

# Add the src directory to PYTHONPATH so modules can import each other directly
ENV PYTHONPATH=/app/src

# Start the FastAPI server via Uvicorn on port 9000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
