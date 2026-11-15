# Thẩm Tra Mô Hình BIM - bản web
# Chạy được trên: máy chủ công ty (Docker), Hugging Face Spaces (Docker), Render, Railway, VPS...
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ifc_checker ./ifc_checker
COPY webapp ./webapp
COPY templates ./templates
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 7860
# Biến môi trường tuỳ chọn: ANTHROPIC_API_KEY (để trợ lý AI dùng chung key của máy chủ)
CMD ["sh", "-c", "uvicorn webapp.server:app --host 0.0.0.0 --port ${PORT}"]
