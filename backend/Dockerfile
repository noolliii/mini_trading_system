FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# lightgbm · xgboost(ml_automl.py 용)가 내부적으로 쓰는 OpenMP 공유 라이브러리입니다.
# 이게 없으면 import 단계에서 "libgomp.so.1: cannot open shared object file" 로 죽습니다.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
