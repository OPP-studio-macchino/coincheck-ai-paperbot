FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd -m -u 10001 paperbot
COPY --chown=paperbot:paperbot app /srv/app
USER paperbot
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
