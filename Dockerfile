# Use the official Playwright base image — Chromium + all deps pre-installed
# This avoids the font package issues on Debian Trixie
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

# Set working directory
WORKDIR /app

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY app.py .

# Expose port
EXPOSE 8080

# Run the service
CMD ["python", "app.py"]
