FROM python:3.12-slim

WORKDIR /app

# 의존성 레이어 캐싱을 위해 requirements.txt만 먼저 복사
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
