FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python health_check.py

CMD ["python", "main.py"]
