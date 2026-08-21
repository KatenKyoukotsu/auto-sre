FROM python:3.12-slim

WORKDIR /app

COPY sre-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sre-agent/*.py ./
COPY frontend/ ./static/

EXPOSE 8096

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8096"]
