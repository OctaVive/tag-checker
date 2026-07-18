FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUSIC_PATH=/music \
    PORT=5000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py scanner.py tags.py ./
COPY templates ./templates

EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "--threads", "4", "--timeout", "120", "app:app"]
