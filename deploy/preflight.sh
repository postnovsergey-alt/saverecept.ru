#!/usr/bin/env bash
# Проверки перед деплоем. Возвращает ненулевой код, если запускаться нельзя.
# Вызывается автоматически из deploy.sh, но можно и руками.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Требования Самобранки. Сборка образа — самый прожорливый момент.
NEED_MEM_BUILD=900     # МБ свободной памяти на время сборки
NEED_MEM_MIN=400       # ниже этого не стартуем вообще
NEED_DISK=2500         # МБ под образ, базу и картинки
LOAD_LIMIT_PER_CPU=2.0 # если машина уже в потолке, сборка добьёт соседей

FAILED=0
WARNED=0

fail() { bad "$1"; FAILED=1; }
soft() { warn "$1"; WARNED=1; }

say "${C_BOLD}Проверки перед деплоем${C_RESET}"

# --------------------------------------------------------- инструменты
head1 "Инструменты"
docker_ready && ok "docker отвечает" || fail "docker недоступен — деплоить нечем"
compose version >/dev/null 2>&1 && ok "compose на месте" || fail "docker compose не найден"
have curl && ok "curl есть" || fail "curl нужен для проверок здоровья: apt install curl"
have ss || soft "ss не установлен, проверка портов будет неполной: apt install iproute2"

# --------------------------------------------------------- ресурсы
head1 "Ресурсы"
FREE_MEM=$(mem_free_mb)
TOTAL_MEM=$(mem_total_mb)
FREE_DISK=$(disk_free_mb)
CPUS=$(cpu_count)
LOAD=$(load_1min)

if [ "$FREE_MEM" -ge "$NEED_MEM_BUILD" ]; then
  ok "память: $FREE_MEM МБ свободно из $TOTAL_MEM МБ"
elif [ "$FREE_MEM" -ge "$NEED_MEM_MIN" ]; then
  soft "память: $FREE_MEM МБ — на сборку впритык"
  dim "если сборка упадёт по OOM, соберите образ локально и привезите:"
  dim "  docker save samobranka-web | ssh СЕРВЕР docker load"
else
  fail "память: всего $FREE_MEM МБ свободно, сборка задушит соседей"
fi

if [ "$FREE_DISK" -ge "$NEED_DISK" ]; then
  ok "диск: $FREE_DISK МБ свободно"
elif [ "$FREE_DISK" -ge 1200 ]; then
  soft "диск: $FREE_DISK МБ — почистите мусор: docker system prune -f"
else
  fail "диск: $FREE_DISK МБ, для образа не хватит"
fi

LOAD_OK=$(awk -v l="$LOAD" -v c="$CPUS" -v lim="$LOAD_LIMIT_PER_CPU" \
          'BEGIN { print (l <= c * lim) ? 1 : 0 }')
if [ "$LOAD_OK" = "1" ]; then
  ok "загрузка: $LOAD на $CPUS ядрах"
else
  soft "загрузка $LOAD на $CPUS ядрах — машина уже занята, сборка будет долгой"
fi

# --------------------------------------------------------- своп
if [ -r /proc/swaps ] && [ "$(wc -l < /proc/swaps)" -gt 1 ]; then
  ok "своп подключён — переживём пик сборки"
elif [ "$FREE_MEM" -lt "$NEED_MEM_BUILD" ]; then
  soft "свопа нет и памяти мало — риск, что OOM killer прибьёт чужой процесс"
fi

# --------------------------------------------------------- конфигурация
head1 "Конфигурация"
if [ -f "$PROJECT_DIR/.env" ]; then
  ok ".env на месте"
  set -a; source "$PROJECT_DIR/.env" 2>/dev/null || true; set +a

  [ -n "${SECRET_KEY:-}" ] && [ "${SECRET_KEY}" != "dev-secret-change-me" ] \
    && ok "SECRET_KEY задан" || fail "SECRET_KEY пустой или дефолтный"

  [ -n "${LLM_API_KEY:-}" ] \
    && ok "ключ LLM задан" \
    || soft "ключ LLM пустой — страницы без разметки разберутся хуже"

  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] \
    && ok "токен бота задан" \
    || soft "токен бота пустой — контейнер бота будет перезапускаться вхолостую"
else
  fail ".env отсутствует — запустите ./deploy/adopt_env.sh"
fi

# --------------------------------------------------------- порт
head1 "Порт"
WANT_PORT="${WEB_PORT:-8080}"
if port_busy "$WANT_PORT"; then
  # это может быть наш же предыдущий контейнер — тогда всё нормально
  if docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | grep -q "samobranka-web.*$WANT_PORT"; then
    ok "порт $WANT_PORT занят нашим же контейнером — это обновление"
  else
    ALT=$(pick_free_port $((WANT_PORT + 1)) || echo "")
    fail "порт $WANT_PORT занят соседом. Свободен $ALT — поправьте WEB_PORT в .env"
  fi
else
  ok "порт $WANT_PORT свободен"
fi

# --------------------------------------------------------- итог
head1 "Итог"
if [ "$FAILED" = "1" ]; then
  bad "деплоить нельзя, сначала разберитесь с отмеченным выше"
  exit 1
fi
if [ "$WARNED" = "1" ]; then
  warn "есть замечания, но запуститься можно"
  exit 0
fi
ok "всё чисто, можно деплоить"
