FROM python:3.11-slim
WORKDIR /srv/nextcomply
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV APP_ENV=development DATABASE_PATH=/srv/nextcomply/data/nextcomply.db UPLOAD_DIR=/srv/nextcomply/data/uploads
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
