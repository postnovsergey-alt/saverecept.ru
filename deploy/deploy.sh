#!/usr/bin/env bash
# Деплой Самобранки на общий сервер.
#
#   ./deploy/deploy.sh              обычный запуск, с вопросами
#   ./deploy/deploy.sh --yes        без вопросов
#   ./deploy/deploy.sh --dry-run    только проверки, ничего не запускать
#
# Порядок такой: проверить ресурсы, запомнить состояние соседей, сохранить
# текущий образ на случай отката, собрать, поднять, дождаться здоровья,
# сверить соседей. Если сломались мы или соседи — автоматический откат.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --yes) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
  esac
done

cd "$PROJECT_DIR"
STARTED_AT=$(date +%s)

say "${C_BOLD}Деплой Самобранки${C_RESET}  $(date '+%d.%m.%Y %H:%M')"
say "${C_DIM}Проект: $PROJECT_DIR${C_RESET}"

# ============================================================ 1. проверки
head1 "1/6 Проверки"
if ! bash "$PROJECT_DIR/deploy/preflight.sh"; then
  bad "Деплой остановлен на проверках."
  exit 1
fi

if [ "$DRY_RUN" = "1" ]; then
  say ""
  ok "Сухой прогон закончен. Проверки пройдены, ничего не запускалось."
  exit 0
fi

# ============================================================ 2. соседи до
head1 "2/6 Запоминаю состояние соседей"
bash "$PROJECT_DIR/deploy/neighbors.sh" snapshot

# ============================================================ 3. точка отката
head1 "3/6 Готовлю точку отката"
PREV_IMAGE=""
if docker image inspect samobranka-web:latest >/dev/null 2>&1; then
  PREV_IMAGE="samobranka-web:rollback"
  docker tag samobranka-web:latest "$PREV_IMAGE"
  ok "предыдущий образ помечен как $PREV_IMAGE"
else
  dim "предыдущего образа нет — это первый запуск, откатываться будет некуда"
fi

if [ -d "$PROJECT_DIR/data" ]; then
  BACKUP="$STATE_DIR/data-$(date +%Y%m%d-%H%M%S).tar.gz"
  tar czf "$BACKUP" -C "$PROJECT_DIR" data 2>/dev/null \
    && ok "база и картинки в резервной копии: $(basename "$BACKUP")" \
    || warn "не удалось сделать копию data (не критично для первого запуска)"
  # держим только 5 последних копий
  ls -1t "$STATE_DIR"/data-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
fi

# ============================================================ 4. сборка
head1 "4/6 Сборка образа"
say "${C_DIM}Это самый тяжёлый шаг. Соседи ограничены не будут: сборка идёт${C_RESET}"
say "${C_DIM}в докере, а контейнеры потом получат лимиты из docker-compose.yml.${C_RESET}"

if ! compose build 2>&1 | tail -20; then
  bad "сборка не удалась"
  exit 1
fi
ok "образ собран"

# ============================================================ 5. запуск
head1 "5/6 Запуск"
set -a; source "$PROJECT_DIR/.env" 2>/dev/null || true; set +a
PORT="${WEB_PORT:-8080}"

if ! compose up -d 2>&1 | tail -10; then
  bad "не удалось поднять контейнеры"
  bash "$PROJECT_DIR/deploy/rollback.sh" --auto
  exit 1
fi

printf '  ждём, пока сайт ответит'
HEALTHY=0
for _ in $(seq 1 30); do
  code=$(http_code "http://127.0.0.1:$PORT/healthz" 4)
  if [ "$code" = "200" ]; then HEALTHY=1; break; fi
  printf '.'
  sleep 2
done
printf '\n'

if [ "$HEALTHY" = "1" ]; then
  ok "сайт отвечает на http://127.0.0.1:$PORT/healthz"
  dim "$(curl -s --max-time 4 "http://127.0.0.1:$PORT/healthz")"
else
  bad "сайт не поднялся за минуту"
  say ""
  say "  Последние строки лога:"
  compose logs --tail 30 web 2>&1 | sed 's/^/    /'
  bash "$PROJECT_DIR/deploy/rollback.sh" --auto
  exit 1
fi

# бот может не подниматься, если токена нет — это не повод откатываться
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  sleep 3
  if docker ps --format '{{.Names}}' | grep -q samobranka-bot; then
    ok "бот запущен"
  else
    warn "бот не поднялся — проверьте токен: docker compose logs bot"
  fi
else
  dim "бот пропущен: TELEGRAM_BOT_TOKEN не задан"
fi

# ============================================================ 6. соседи после
head1 "6/6 Проверяю соседей"
sleep 4
if bash "$PROJECT_DIR/deploy/neighbors.sh" compare; then
  :
else
  bad "После нашего запуска у соседей что-то изменилось."
  if confirm "  Откатить Самобранку?"; then
    bash "$PROJECT_DIR/deploy/rollback.sh" --auto
    exit 1
  fi
  warn "оставляю как есть по вашему решению"
fi

head1 "Проверка ресурсов после запуска"
docker stats --no-stream --format '  {{.Name}}: память {{.MemUsage}}, CPU {{.CPUPerc}}' 2>/dev/null \
  | grep samobranka || dim "статистика недоступна"
dim "свободно памяти: $(mem_free_mb) МБ, диска: $(disk_free_mb) МБ"

# ============================================================ итог
ELAPSED=$(( $(date +%s) - STARTED_AT ))
head1 "Готово за ${ELAPSED} с"
say "  Сайт:   ${PUBLIC_BASE_URL:-http://127.0.0.1:$PORT}"
say "  Локально: http://127.0.0.1:$PORT"
say ""
say "  ${C_DIM}Логи:    docker compose logs -f web${C_RESET}"
say "  ${C_DIM}Откат:   ./deploy/rollback.sh${C_RESET}"
say "  ${C_DIM}Демо:    docker compose exec web python -m tools.demo_seed${C_RESET}"
say "  ${C_DIM}LLM:     docker compose exec web python -m tools.check_llm${C_RESET}"

if [ -z "${PUBLIC_BASE_URL##http://127.0.0.1*}" ] || [ -z "${PUBLIC_BASE_URL:-}" ]; then
  say ""
  warn "Порт открыт только на localhost — снаружи сайт пока не виден."
  dim "Пример конфига для nginx: deploy/nginx.conf.example"
fi
