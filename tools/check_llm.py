"""Проверка LLM-провайдеров.

  python -m tools.check_llm           проверить, что настроенные модели отвечают
  python -m tools.check_llm --list    показать, какие модели доступны по ключу

Второе полезно потому, что идентификаторы моделей у Google периодически
меняются: сегодня работает одно имя, через полгода — другое.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import LLM_PROVIDERS  # noqa: E402
from app.parser.llm import ping       # noqa: E402


def list_models() -> None:
    if not LLM_PROVIDERS:
        print("Ни один провайдер не настроен. Заполните LLM_API_KEY и LLM_MODEL в .env")
        return
    for p in LLM_PROVIDERS:
        print(f"\n=== {p['name']}: {p['base_url']} ===")
        try:
            with httpx.Client(timeout=30) as c:
                r = c.get(f"{p['base_url']}/models",
                          headers={"Authorization": f"Bearer {p['api_key']}"})
                r.raise_for_status()
                items = r.json().get("data", [])
        except Exception as e:  # noqa: BLE001
            print(f"  не удалось получить список: {e}")
            continue
        names = sorted(m.get("id", "") for m in items)
        flash = [n for n in names if "flash" in n.lower()]
        if flash:
            print("  подходящие для разбора (быстрые и бесплатные):")
            for n in flash:
                print(f"    {n}")
        print(f"  всего моделей: {len(names)}")


def main() -> int:
    if "--list" in sys.argv:
        list_models()
        return 0

    if not LLM_PROVIDERS:
        print("LLM не настроен — рецепты будут разбираться разметкой и эвристикой.")
        print("Это рабочий режим, просто страницы без разметки будут разбираться хуже.")
        return 0

    bad = 0
    for entry in ping():
        mark = "ok  " if entry["ok"] else "FAIL"
        print(f"  {mark} {entry['name']}: {entry['model']} — {entry['detail']}")
        if not entry["ok"]:
            bad += 1

    if bad:
        print("\nЕсли пишет про модель — посмотрите доступные: python -m tools.check_llm --list")
    return 1 if bad and bad == len(LLM_PROVIDERS) else 0


if __name__ == "__main__":
    sys.exit(main())
