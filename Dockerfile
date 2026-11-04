# Python 3.12 runtime; no coincheck private exchange adapter.
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
ARG APP_GIT_SHA=unknown
LABEL org.opencontainers.image.revision="${APP_GIT_SHA}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.lock /srv/requirements.lock
RUN pip install --disable-pip-version-check -r /srv/requirements.lock \
    && useradd -m -u 10001 paperbot
COPY --chown=paperbot:paperbot app /srv/app
COPY --chown=paperbot:paperbot migrations /srv/migrations
USER paperbot
CMD ["uvicorn","app.api:app","--host","0.0.0.0","--port","8000"]
