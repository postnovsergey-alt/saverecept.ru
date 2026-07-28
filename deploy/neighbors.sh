#!/usr/bin/env bash
# Снимок здоровья соседних сервисов.
#
#   ./deploy/neighbors.sh snapshot   запомнить, как всё выглядит сейчас
#   ./deploy/neighbors.sh compare    сверить с запомненным и показать различия
#
# Смысл: если после нашего деплоя у соседа контейнер перезапустился, упал или
# перестал отвечать на своём порту — мы это увидим сразу, а не через неделю.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SNAP="$STATE_DIR/neighbors.snapshot"
OURS="samobranka-web samobranka-bot"

is_ours() { case " $OURS " in *" $1 "*) return 0;; esac; return 1; }

collect() {
  # формат строки: тип|имя|состояние|доп
  if docker_ready; then
    docker ps -a --format '{{.Names}}\t{{.State}}\t{{.RestartCount}}' 2>/dev/null \
    | while IFS=$'\t' read -r name state restarts; do
        is_ours "$name" && continue
        printf 'docker|%s|%s|restarts=%s\n' "$name" "$state" "${restarts:-0}"
      done
  fi

  if have systemctl; then
    systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null \
    | awk '{print $1}' \
    | grep -viE '^(systemd|dbus|cron|ssh|rsyslog|networkd|resolved|udev|polkit|getty|snapd|unattended|accounts|multipathd|irqbalance|chrony|ufw|containerd|walinuxagent|qemu|serial)' \
    | while read -r unit; do
        printf 'systemd|%s|active|\n' "$unit"
      done
  fi

  # слушающие порты — самый честный признак «сервис жив»
  if have ss; then
    ss -Hltn 2>/dev/null | awk '{split($4,a,":"); print a[length(a)]}' | sort -un \
    | while read -r p; do
        printf 'port|%s|listen|\n' "$p"
      done
  fi
}

case "${1:-compare}" in
  snapshot)
    collect | sort > "$SNAP"
    n=$(wc -l < "$SNAP" | tr -d ' ')
    ok "снимок соседей сохранён: $n записей"
    dim "$SNAP"
    ;;

  compare)
    if [ ! -f "$SNAP" ]; then
      warn "снимка нет — сравнивать не с чем (сначала snapshot)"
      exit 0
    fi
    CURRENT="$STATE_DIR/neighbors.current"
    collect | sort > "$CURRENT"

    LOST=$(comm -23 "$SNAP" "$CURRENT")
    NEW=$(comm -13 "$SNAP" "$CURRENT")

    problems=0
    if [ -n "$LOST" ]; then
      # исчезнувшие записи — это плохо: сервис или порт пропал
      while IFS='|' read -r kind name state extra; do
        [ -z "$kind" ] && continue
        case "$kind" in
          docker)  bad "контейнер соседа изменился или пропал: $name (было: $state $extra)"; problems=1;;
          systemd) bad "сервис перестал быть активным: $name"; problems=1;;
          port)    bad "порт $name больше никто не слушает"; problems=1;;
        esac
      done <<< "$LOST"
    fi

    if [ -n "$NEW" ]; then
      while IFS='|' read -r kind name state extra; do
        [ -z "$kind" ] && continue
        case "$kind" in
          docker)  dim "новое состояние контейнера: $name — $state $extra";;
          port)    dim "появился слушатель на порту $name";;
        esac
      done <<< "$NEW"
    fi

    if [ "$problems" = "0" ]; then
      ok "соседи в том же состоянии, что и до деплоя"
    fi
    exit "$problems"
    ;;

  *)
    say "Использование: $0 {snapshot|compare}"
    exit 2
    ;;
esac
