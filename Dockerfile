FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create a non-root user (Hugging Face / Railway Good Practice)
RUN useradd -m -u 1000 user

# Copy the rest of the application with ownership
COPY --chown=user:user . .

# Create directory for sqlite database with write permissions
RUN mkdir -p /app/data && chown user:user /app/data

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# Expose single port for Railway
EXPOSE 8080

# Command to run the application
CMD uvicorn main:app --host 0.0.0.0 --port 8080
