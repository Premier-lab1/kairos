#!/usr/bin/env bash
# Проверка ARM-совместимости стека (шаг 4 из Kairos.md).
# В чистом контейнере python:3.12-slim под linux/arm64 ставит каждую ключевую библиотеку
# отдельно с --only-binary=:all: (без сборки из исходников), затем пробует её импортировать.
# Печатает таблицу OK/FAIL; код возврата 1, если хоть одна библиотека не прошла.
#
# Запуск:  bash deploy/check-arm-stack.sh
# На самом сервере CAX21 (ARM) работает нативно. На обычном x86-ПК с Docker нужен эмулятор
# (Docker Desktop его включает сам) — будет медленнее. Другая платформа: PLATFORM=linux/amd64 bash ...

set -u

PLATFORM="${PLATFORM:-linux/arm64}"
IMAGE="${IMAGE:-python:3.12-slim}"

if ! command -v docker >/dev/null 2>&1; then
    echo "docker не найден. Установите Docker (см. deploy/README.md, раздел про Docker)." >&2
    exit 2
fi

echo "Образ: $IMAGE, платформа: $PLATFORM"
echo

# Скрипт, который выполняется ВНУТРИ контейнера. Формат элемента: "pip-имя|имя для import".
read -r -d '' INNER <<'INNER_EOF'
set -u
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

LIBS=(
  "solders|solders"
  "solana|solana"
  "grpcio|grpc"
  "grpcio-tools|grpc_tools"
  "websockets|websockets"
  "aiohttp|aiohttp"
  "orjson|orjson"
  "fastapi|fastapi"
  "pydantic|pydantic"
  "asyncpg|asyncpg"
  "psycopg[binary]|psycopg"
  "SQLAlchemy|sqlalchemy"
  "redis|redis"
  "anthropic|anthropic"
  "aiogram|aiogram"
  "numpy|numpy"
  "pandas|pandas"
  "scikit-learn|sklearn"
  "lightgbm|lightgbm"
  "xgboost|xgboost"
  "httpx|httpx"
  "construct|construct"
)

echo "Архитектура контейнера: $(uname -m), Python $(python -V 2>&1 | cut -d' ' -f2)"
echo
printf '%-20s %-6s %s\n' "БИБЛИОТЕКА" "ИТОГ" "ВЕРСИЯ / ПРИЧИНА"
printf '%-20s %-6s %s\n' "--------------------" "------" "------------------------------"

fail=0
tails=""
for item in "${LIBS[@]}"; do
  pkg="${item%%|*}"
  mod="${item##*|}"
  name="${pkg%%\[*}"

  log=$(pip install --only-binary=:all: "$pkg" 2>&1 </dev/null)
  rc=$?
  if [ $rc -ne 0 ]; then
    fail=1
    printf '%-20s %-6s %s\n' "$pkg" "FAIL" "pip install (нет готовой сборки под эту платформу?)"
    tails+=$'\n'"--- $pkg: pip install ---"$'\n'"$(printf '%s\n' "$log" | tail -n 4 | cut -c1-220)"$'\n'
    continue
  fi

  ierr=$(python -c "import $mod" 2>&1 </dev/null)
  rc=$?
  if [ $rc -ne 0 ]; then
    fail=1
    printf '%-20s %-6s %s\n' "$pkg" "FAIL" "установилась, но import не проходит"
    tails+=$'\n'"--- $pkg: import $mod ---"$'\n'"$(printf '%s\n' "$ierr" | tail -n 4 | cut -c1-220)"
    case "$pkg" in
      lightgbm|xgboost) tails+=$'\n'"(подсказка: возможно, нужен системный пакет libgomp1: apt-get install -y libgomp1)" ;;
    esac
    tails+=$'\n'
    continue
  fi

  ver=$(python -c "import importlib.metadata as m; print(m.version('$name'))" 2>/dev/null </dev/null)
  printf '%-20s %-6s %s\n' "$pkg" "OK" "$ver"
done

if [ -n "$tails" ]; then
  echo
  echo "===== Хвост ошибок ====="
  printf '%s\n' "$tails"
fi

echo
if [ $fail -eq 0 ]; then
  echo "ИТОГ: все библиотеки установились и импортируются."
else
  echo "ИТОГ: есть FAIL — см. таблицу и хвосты ошибок выше."
fi
exit $fail
INNER_EOF

docker run --rm --platform "$PLATFORM" "$IMAGE" bash -c "$INNER"
exit $?
