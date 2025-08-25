# Use official Python 3.11.8 image
FROM python:3.11.8-slim

# Set working directory
WORKDIR /app

# Copy requirements first (for caching)
COPY requirements.txt .

# Upgrade pip, install wheel, and install dependencies
RUN pip install --upgrade pip wheel \
    && pip install numpy==1.26.4 \
    && pip install --no-cache-dir -r requirements.txt

# Copy all project files
COPY . .

# Expose port (Render sets $PORT automatically)
ENV PORT=10000
EXPOSE $PORT

# Set the command to run Gunicorn with your Flask app
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "app:app"]
