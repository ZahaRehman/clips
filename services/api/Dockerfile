# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-noto-core fonts-wqy-microhei libglib2.0-0 libgl1 libgles2 libegl1 libgomp1 \
  && rm -rf /var/lib/apt/lists/*

# Dependencies get their own layer keyed on pyproject.toml only, so code edits don't reinstall them.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
  python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
  && pip install --timeout 180 --retries 8 -r /tmp/requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY entrypoint.sh /entrypoint.sh
RUN pip install --no-cache-dir --no-deps . && chmod +x /entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV FONTS_DIR=/app/app/assets/fonts
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
