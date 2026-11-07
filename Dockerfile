# УралWay API — образ для продакшена. Запуск от непривилегированного пользователя (не root).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ENV=production

WORKDIR /code

# Системный пользователь без пароля, без домашней папки и без shell
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

# Сначала зависимости — так сборка использует кэш слоёв
COPY requirements.txt .
RUN pip install -r requirements.txt

# Код приложения принадлежит app, остальное остаётся у root (только чтение)
COPY --chown=app:app app ./app

USER app

EXPOSE 8000

# Healthcheck использует GET /api/health (порт берётся из $PORT, если платформа его задаёт)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'), timeout=4).status == 200 else 1)"

# --proxy-headers: корректный IP клиента за прокси платформы
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
