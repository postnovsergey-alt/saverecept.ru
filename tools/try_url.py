"""Прогнать одну ссылку через парсер и посмотреть, что получилось.

  python -m tools.try_url https://eda.ru/recepty/...

Ничего не сохраняет в базу и не качает картинки — только показывает разбор.
Удобно, когда какой-то сайт разбирается криво и надо понять, на каком шаге.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.categories import category_title            # noqa: E402
from app.parser.ingredients import format_amount     # noqa: E402
from app.parser.pipeline import ParseError, parse_url  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("Укажите ссылку: python -m tools.try_url https://...")
        return 2

    url = sys.argv[1]
    print(f"Разбираю {url}\n")

    try:
        r = parse_url(url, download_images=False)
    except ParseError as e:
        print(f"Не получилось: {e}")
        return 1

    print(f"Название:  {r.title}")
    print(f"Категория: {category_title(r.category)} "
          f"(уверенность {r.category_confidence})")
    print(f"Способ:    {r.parse_method}")
    if r.total_minutes:
        print(f"Время:     {r.total_minutes} мин")
    if r.servings:
        print(f"Выход:     {r.servings}")
    print(f"Источник:  {r.source_url}")

    print(f"\nПродукты ({len(r.ingredients)}):")
    for i in r.ingredients:
        amount = format_amount(i["amount"], i["unit"])
        note = f"  [{i['note']}]" if i["note"] else ""
        print(f"  - {i['name']:<40} {amount}{note}")
        if not i["amount"] and not i["unit"]:
            print(f"      {'':<40} сырая строка: {i['raw']}")

    print(f"\nШаги ({len(r.steps)}):")
    for n, s in enumerate(r.steps, 1):
        print(f"  {n}. {s[:160]}{'…' if len(s) > 160 else ''}")

    print(f"\nКартинки ({len(r.images)}):")
    for img in r.images:
        print(f"  {img['source_url']}")

    problems = []
    if len(r.ingredients) < 2:
        problems.append("продуктов подозрительно мало")
    if len(r.steps) < 2:
        problems.append("шагов подозрительно мало")
    if not r.images:
        problems.append("картинку не нашли")
    if r.category_confidence < 0.5:
        problems.append("в категории не уверены")
    if problems:
        print("\nНа что обратить внимание: " + "; ".join(problems))
    else:
        print("\nРазбор выглядит полным.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
