FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY public ./public
RUN useradd --create-home appuser && mkdir downloads && chown appuser:appuser downloads
USER appuser
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
