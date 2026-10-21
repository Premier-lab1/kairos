# Kairos backend. Образ мультиархитектурный (amd64 + arm64): python:3.12-slim.
# Сборка: из каталога deploy/ командой `docker compose build` (контекст — каталог backend/).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Непривилегированный пользователь: процесс внутри контейнера не работает от root.
RUN groupadd --system --gid 1000 kairos \
    && useradd --system --uid 1000 --gid kairos --home-dir /app --shell /usr/sbin/nologin kairos

WORKDIR /app

# 1) Зависимости из pyproject.toml — отдельным слоем, чтобы кэш не сбрасывался при правке кода.
#    --only-binary=:all: — если под платформу нет готовой сборки (wheel), билд падает сразу,
#    а не пытается компилировать из исходников.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --only-binary=:all: -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# 2) Код и миграции. Пакет ставится в режиме editable: kairos/db.py ищет миграции
#    как <каталог пакета>/../migrations, то есть /app/migrations — это работает только
#    пока исходники лежат в /app, а не копируются в site-packages.
COPY kairos ./kairos
COPY migrations ./migrations
RUN pip install --no-deps -e .

USER kairos

# Сначала применяются новые миграции, затем запускается конвейер (exec — чтобы
# SIGTERM от `docker stop` доходил до Python). Модуль kairos.pipeline.run пишется отдельно.
CMD ["sh", "-c", "python -m kairos.db && exec python -m kairos.pipeline.run"]
