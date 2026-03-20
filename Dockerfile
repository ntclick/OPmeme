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

# Copy DNS resolution entrypoint
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# User permission setup (Note: root is required to modify /etc/hosts at runtime)
RUN chown -R user:user /app
RUN mkdir -p /app/data && chown user:user /app/data

# Railway standard configuration
EXPOSE 8080

# Use entrypoint to apply runtime fixes
CMD ["./entrypoint.sh"]
