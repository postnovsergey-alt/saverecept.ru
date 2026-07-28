#!/usr/bin/env bash
# Откат: гасим наши контейнеры и, если есть, возвращаем предыдущий образ.
# Соседей не трогаем ни при каких обстоятельствах.
#
#   ./deploy/rollback.sh          с подтверждением
#   ./deploy/rollback.sh --auto   без вопросов (вызывается из deploy.sh)

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
[ "${1:-}" = "--auto" ] && ASSUME_YES=1

say "${C_BOLD}Откат Самобранки${C_RESET}"

if ! confirm "  Погасить контейнеры Самобранки?"; then
  say "  Отменено."
  exit 0
fi

head1 "Гашу свои контейнеры"
compose down 2>&1 | tail -5 | sed 's/^/  /'
ok "samobranka-web и samobranka-bot остановлены"

head1 "Возвращаю предыдущий образ"
if docker image inspect samobranka-web:rollback >/dev/null 2>&1; then
  docker tag samobranka-web:rollback samobranka-web:latest
  ok "образ откачен на предыдущий"
  if confirm "  Поднять предыдущую версию?"; then
    compose up -d 2>&1 | tail -5 | sed 's/^/  /'
    sleep 5
    set -a; source "$PROJECT_DIR/.env" 2>/dev/null || true; set +a
    code=$(http_code "http://127.0.0.1:${WEB_PORT:-8080}/healthz" 6)
    [ "$code" = "200" ] && ok "предыдущая версия поднялась" \
                        || bad "предыдущая версия тоже не отвечает (код $code)"
  fi
else
  dim "предыдущего образа нет — просто оставляю всё погашенным"
fi

head1 "Проверяю соседей"
bash "$PROJECT_DIR/deploy/neighbors.sh" compare || \
  bad "у соседей всё ещё расхождения — посмотрите вручную: docker ps -a"

head1 "Резервные копии данных"
ls -1t "$STATE_DIR"/data-*.tar.gz 2>/dev/null | head -5 | sed 's/^/  /' \
  || dim "копий нет"
say ""
dim "Восстановить данные: tar xzf ФАЙЛ -C $PROJECT_DIR"
