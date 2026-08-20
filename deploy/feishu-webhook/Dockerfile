FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx

COPY feishu_gateway.py .

EXPOSE 5001

CMD ["uvicorn", "feishu_gateway:app", "--host", "0.0.0.0", "--port", "5001"]
