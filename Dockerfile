FROM mcr.microsoft.com/playwright/python:v1.58.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
RUN mkdir -p /app/data /app/screenshots /app/logs \
    && chown -R 1000:1000 /app/data /app/screenshots /app/logs
ENV PYTHONUNBUFFERED=1
USER 1000:1000
CMD ["python", "-m", "src.main"]
