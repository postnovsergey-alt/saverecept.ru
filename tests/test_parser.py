"""Проверка пайплайна на страницах трёх типов: JSON-LD, микроразметка, голый HTML.

Запуск: python -m tests.test_parser
Сеть не нужна — страницы лежат в fixtures, картинки не скачиваются.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parser import llm as llm_module           # noqa: E402
from app.parser.classify import classify           # noqa: E402
from app.parser.ingredients import format_amount, parse_ingredient_line  # noqa: E402
from app.parser.pipeline import parse_html, parse_image, ParseError  # noqa: E402
from app.utils import normalize_url, slugify, url_key  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
failures: list[str] = []


def check(condition: bool, label: str, got=None) -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f"  -> получили: {got!r}" if got is not None else ""))
        failures.append(label)


def parse_fixture(name: str, url: str):
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return parse_html(html, url, use_llm=False)


def test_jsonld():
    print("\n[1] schema.org JSON-LD (так размечено большинство сайтов)")
    r = parse_fixture("jsonld_borsch.html", "https://example.ru/recipes/borsch?utm_source=tg")
    check(r.parse_method == "jsonld", "разобрано через JSON-LD", r.parse_method)
    check(r.title == "Борщ украинский с пампушками", "название", r.title)
    check(r.category == "soups", "категория: супы", r.category)
    check(r.category_confidence > 0.5, "уверенность в категории", r.category_confidence)
    check(len(r.ingredients) == 9, "9 ингредиентов", len(r.ingredients))
    check(r.total_minutes == 135, "время 2 ч 15 мин -> 135", r.total_minutes)
    check(r.servings == "6 порций", "выход", r.servings)
    check(len(r.steps) == 6, "5 шагов + заголовок раздела «Бульон»", len(r.steps))
    check(r.steps[0].startswith("—"), "раздел «Бульон» сохранён", r.steps[0])
    check(len(r.images) >= 2, "картинки из разметки + og:image", len(r.images))
    check("utm_source" not in r.source_url, "utm-хвост отрезан", r.source_url)

    beef = r.ingredients[0]
    check(beef["name"] == "Говядина на кости", "название ингредиента", beef["name"])
    check(beef["amount"] == 600 and beef["unit"] == "г", "количество 600 г",
          (beef["amount"], beef["unit"]))
    salt = r.ingredients[-1]
    check(salt["note"] == "по вкусу", "«по вкусу» ушло в примечание", salt["note"])


def test_microdata():
    print("\n[2] Микроразметка itemprop")
    r = parse_fixture("microdata_salad.html", "https://example.ru/salat-cezar/")
    check(r.parse_method == "microdata", "разобрано через микроразметку", r.parse_method)
    check(r.title == "Салат Цезарь с курицей", "название", r.title)
    check(r.category == "salads", "категория: салаты", r.category)
    check(len(r.ingredients) == 7, "7 ингредиентов", len(r.ingredients))
    check(len(r.steps) == 4, "4 шага", len(r.steps))
    check(r.total_minutes == 35, "время 35 минут", r.total_minutes)
    oil = [i for i in r.ingredients if "асло" in i["name"]][0]
    check(oil["unit"] == "ст. л." and oil["amount"] == 3, "«3 ст. л.» без тире",
          (oil["amount"], oil["unit"]))
    garlic = [i for i in r.ingredients if "еснок" in i["name"]][0]
    check(garlic["unit"] == "зубчик", "штучная единица «зубчика»", garlic["unit"])


def test_plain_html():
    print("\n[3] Страница без разметки — эвристика по заголовкам")
    r = parse_fixture("plain_pie.html", "https://blog.example.ru/sharlotka")
    check(r.parse_method == "heuristic", "сработала эвристика", r.parse_method)
    check(r.title == "Шарлотка с яблоками", "название из og:title", r.title)
    check(r.category == "baking", "категория: выпечка", r.category)
    check(len(r.ingredients) == 6, "6 ингредиентов", len(r.ingredients))
    check(len(r.steps) == 4, "4 шага без комментариев и рекламы", len(r.steps))
    check(all("спасибо за рецепт" not in s.lower() for s in r.steps),
          "комментарии не попали в шаги")
    check(len(r.images) == 1, "картинка из og:image", len(r.images))
    cinnamon = [i for i in r.ingredients if "орица" in i["name"]][0]
    check(cinnamon["unit"] == "щепотка", "«щепотка» распознана как единица", cinnamon["unit"])


def test_classifier():
    print("\n[4] Классификатор на спорных названиях")
    cases = [
        ("Суп-пюре из тыквы", "soups"),
        ("Картофельное пюре на молоке", "sides"),
        ("Оливье с говядиной", "salads"),
        ("Куриные котлеты с сыром", "main"),
        ("Огурцы малосольные на зиму", "preserves"),
        ("Соус песто из базилика", "sauces"),
        ("Домашний лимонад с мятой", "drinks"),
        ("Овсяная каша с бананом на завтрак", "breakfast"),
        ("Шоколадный чизкейк", "desserts"),
        ("Хумус из нута", "snacks"),
        ("Гречка с грибами", "sides"),
        ("Плов из баранины", "main"),
        ("Пирожки с капустой", "baking"),
        ("Уха из речной рыбы", "soups"),
    ]
    for title, expected in cases:
        got, conf = classify(title)
        check(got == expected, f"«{title}» -> {expected}", f"{got} ({conf})")


def test_ingredient_parser():
    print("\n[5] Разбор строк ингредиентов")
    cases = [
        ("Куриное филе — 500 г", "Куриное филе", 500.0, "г"),
        ("500 г куриного филе", "куриного филе", 500.0, "г"),
        ("2 ст. л. оливкового масла", "оливкового масла", 2.0, "ст. л."),
        ("Лук репчатый 1 шт.", "Лук репчатый", 1.0, "шт"),
        ("1/2 стакана риса", "риса", 0.5, "стакан"),
        ("Молоко — 1,5 л", "Молоко", 1.5, "л"),
        ("Помидоры — 2-3 шт", "Помидоры", 2.0, "шт"),
    ]
    for raw, name, amount, unit in cases:
        got = parse_ingredient_line(raw)
        ok = got["name"] == name and got["amount"] == amount and got["unit"] == unit
        check(ok, f"«{raw}»", (got["name"], got["amount"], got["unit"]))

    check(format_amount(0.5, "стакан") == "0,5 стакан", "форматирование дробей",
          format_amount(0.5, "стакан"))
    check(parse_ingredient_line("Соль по вкусу")["note"] == "по вкусу", "«по вкусу»")


def test_parse_image():
    print("\n[7] parse_image: LLM-vision путь и дедуп по хэшу")
    import io
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (400, 300), (220, 200, 180)).save(buf, "JPEG")
    fake_photo = buf.getvalue()

    calls = {"n": 0}

    def fake_extract(image_bytes, mime_type="image/jpeg"):
        calls["n"] += 1
        return {
            "title": "Оладьи на кефире",
            "description": "Пышные, из бабушкиной книги.",
            "ingredients": ["Кефир — 500 мл", "Мука — 2 стакана", "Яйцо — 1 шт", "Сахар — 2 ст. л."],
            "steps": ["Смешать сухие ингредиенты.", "Влить кефир и яйцо.", "Жарить с двух сторон."],
            "images": [],
            "total_minutes": 25,
            "servings": "4 порции",
            "site_category": "",
            "llm_category": "breakfast",
            "method": "llm-image",
        }

    original = llm_module.LLM_PROVIDERS
    llm_module.LLM_PROVIDERS = [{"name": "test", "base_url": "x", "api_key": "x", "model": "x"}]
    from app.parser import pipeline as pipeline_module
    original_extract = pipeline_module.extract_from_image
    pipeline_module.extract_from_image = fake_extract
    try:
        r = parse_image(fake_photo, "image/jpeg")
        check(r.title == "Оладьи на кефире", "название взято из LLM", r.title)
        check(len(r.ingredients) == 4, "4 ингредиента", len(r.ingredients))
        check(len(r.steps) == 3, "3 шага", len(r.steps))
        check(r.category == "breakfast", "категория из llm_category", r.category)
        check(r.parse_method == "llm-image", "метод помечен как llm-image", r.parse_method)
        check(r.source_domain == "фото", "домен = 'фото'", r.source_domain)
        check(r.source_key.startswith("photo:"), "source_key с префиксом photo:", r.source_key)
        check(len(r.images) == 1, "исходное фото сохранено", len(r.images))
        check(r.images[0]["filename"].endswith(".webp"), "картинка ужата в webp",
              r.images[0]["filename"])
        check(r.images[0].get("is_source") is True,
              "фото помечено как source (в обложку не пойдёт)", r.images[0].get("is_source"))

        r2 = parse_image(fake_photo, "image/jpeg")
        check(r2.source_key == r.source_key, "тот же файл -> тот же source_key")

        try:
            parse_image(b"", "image/jpeg")
            check(False, "пустые байты -> ParseError")
        except ParseError:
            check(True, "пустые байты -> ParseError")
    finally:
        llm_module.LLM_PROVIDERS = original
        pipeline_module.extract_from_image = original_extract


def test_utils():
    print("\n[6] Ссылки и слаги")
    a = "https://WWW.Example.ru/recipes/borsch/?utm_source=tg&page=2#top"
    b = "http://example.ru/recipes/borsch?page=2"
    check(normalize_url(a) == "https://example.ru/recipes/borsch?page=2", "нормализация", normalize_url(a))
    check(url_key(a) != url_key(b), "разные схемы — разные ключи")
    check(url_key(a) == url_key(a.replace("#top", "")), "якорь не влияет на дедупликацию")
    check(slugify("Борщ украинский") == "borsch-ukrainskiy", "транслитерация", slugify("Борщ украинский"))


if __name__ == "__main__":
    for fn in (test_jsonld, test_microdata, test_plain_html,
               test_classifier, test_ingredient_parser, test_parse_image, test_utils):
        fn()

    print("\n" + "=" * 56)
    if failures:
        print(f"Провалено проверок: {len(failures)}")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("Все проверки пройдены.")
