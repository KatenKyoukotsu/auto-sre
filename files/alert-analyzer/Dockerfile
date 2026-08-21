FROM python:3.12-slim

WORKDIR /app

COPY alert-analyzer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alert-analyzer/*.py ./
COPY alert-analyzer/migrations/ ./migrations/
COPY common/ ./common/

EXPOSE 8097

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8097"]
