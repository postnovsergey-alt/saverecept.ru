"""Разбор строки ингредиента на количество / единицу / название.

Сейчас нужен для аккуратного вида карточки, но главная цель — задел на
планировщик меню: без нормализованных названий и граммовок список покупок
не собрать. Поэтому храним и сырую строку, и разобранную версию.
"""
from __future__ import annotations

import re

VULGAR = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}

UNITS = {
    "г": "г", "гр": "г", "грамм": "г", "грамма": "г", "граммов": "г",
    "кг": "кг", "килограмм": "кг", "килограмма": "кг",
    "мг": "мг",
    "мл": "мл", "миллилитр": "мл", "миллилитров": "мл",
    "л": "л", "литр": "л", "литра": "л", "литров": "л",
    "шт": "шт", "штук": "шт", "штуки": "шт", "штука": "шт",
    "ст.л": "ст. л.", "стл": "ст. л.", "ст. л": "ст. л.", "столовая ложка": "ст. л.",
    "столовых ложки": "ст. л.", "столовые ложки": "ст. л.", "столовых ложек": "ст. л.",
    "ч.л": "ч. л.", "чл": "ч. л.", "ч. л": "ч. л.", "чайная ложка": "ч. л.",
    "чайных ложки": "ч. л.", "чайные ложки": "ч. л.", "чайных ложек": "ч. л.",
    "стакан": "стакан", "стакана": "стакан", "стаканов": "стакан",
    "щепотка": "щепотка", "щепотки": "щепотка",
    "зубчик": "зубчик", "зубчика": "зубчик", "зубчиков": "зубчик",
    "пучок": "пучок", "пучка": "пучок",
    "банка": "банка", "банки": "банка",
    "упаковка": "упак.", "упак": "упак.", "пачка": "пачка",
    "долька": "долька", "веточка": "веточка", "лист": "лист",
    "капля": "капля", "капель": "капля",
}
UNIT_ALTERNATIVES = sorted(UNITS.keys(), key=len, reverse=True)
UNIT_RE = "|".join(re.escape(u) for u in UNIT_ALTERNATIVES)

NUM = r"(?:\d+[.,]\d+|\d+\s*/\s*\d+|\d+|[½¼¾⅓⅔⅛])"
AMOUNT_RE = re.compile(rf"(?P<num>{NUM}(?:\s*[-–]\s*{NUM})?)\s*(?P<unit>(?:{UNIT_RE})\.?)?(?![а-яё])",
                       re.I)
LEADING_RE = re.compile(rf"^\s*{AMOUNT_RE.pattern}\s*", re.I)
TRAILING_RE = re.compile(rf"\s*{AMOUNT_RE.pattern}\s*$", re.I)

NOTE_RE = re.compile(r"\b(по вкусу|для подачи|для смазывания|для жарки|опционально|"
                     r"при желании|для украшения|по желанию)\b", re.I)
DASH_SPLIT_RE = re.compile(r"\s+[—–-]\s+|\s+:\s+|\s{2,}")


def _to_float(raw: str) -> float | None:
    raw = raw.strip().replace(",", ".")
    if not raw:
        return None
    if raw in VULGAR:
        return VULGAR[raw]
    m = re.match(r"^(\d+)\s*/\s*(\d+)$", raw)
    if m:
        denom = float(m.group(2))
        return float(m.group(1)) / denom if denom else None
    m = re.match(rf"^({NUM})\s*[-–]\s*({NUM})$", raw)  # диапазон «2-3» -> берём меньшее
    if m:
        return _to_float(m.group(1))
    try:
        return float(raw)
    except ValueError:
        return None


def _norm_unit(raw: str | None) -> str:
    if not raw:
        return ""
    key = raw.strip().rstrip(".").lower().replace(" ", "")
    return UNITS.get(key, UNITS.get(raw.strip().rstrip(".").lower(), raw.strip()))


def normalize_name(name: str) -> str:
    """Грубая нормализация для группировки: нижний регистр, без уточнений и хвостов."""
    name = name.lower().replace("ё", "е")
    name = re.sub(r"\(.*?\)", " ", name)
    name = re.sub(r"[^a-zа-я0-9\s-]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    stop = {"свежий", "свежая", "свежие", "молотый", "молотая", "крупный", "мелкий",
            "нарезанный", "нарезанная", "измельченный", "очищенный", "large", "small"}
    words = [w for w in name.split() if w not in stop]
    return " ".join(words)[:200]


def parse_ingredient_line(raw: str) -> dict:
    """'Куриное филе — 500 г' -> {name: 'Куриное филе', amount: 500.0, unit: 'г'}"""
    text = re.sub(r"\s+", " ", (raw or "")).strip(" .;,")
    if not text:
        return {"raw": raw or "", "name": "", "name_norm": "", "amount": None, "unit": "", "note": ""}

    note = ""
    note_match = NOTE_RE.search(text)
    if note_match:
        note = note_match.group(0).lower()
        text = NOTE_RE.sub("", text).strip(" —–-,;")

    amount: float | None = None
    unit = ""
    name = text

    parts = [p for p in DASH_SPLIT_RE.split(text) if p.strip()]
    if len(parts) >= 2:
        # «Название — 500 г» или «500 г — Название»
        head, tail = parts[0].strip(), " ".join(parts[1:]).strip()
        for candidate_amount, candidate_name in ((tail, head), (head, tail)):
            m = LEADING_RE.match(candidate_amount)
            if m and m.group("num") and len(re.sub(LEADING_RE, "", candidate_amount).strip()) <= 12:
                amount = _to_float(m.group("num"))
                unit = _norm_unit(m.group("unit")) or re.sub(LEADING_RE, "", candidate_amount).strip()
                name = candidate_name
                break
        else:
            # «Корица — щепотка»: количества нет, но единица сама по себе осмысленна
            bare = tail.strip().rstrip(".").lower()
            if bare in UNITS:
                unit = UNITS[bare]
                name = head
            else:
                name = head

    if amount is None:
        m = LEADING_RE.match(name)
        if m and m.group("num") and (m.group("unit") or re.search(r"[а-яa-z]", name[m.end():], re.I)):
            amount = _to_float(m.group("num"))
            unit = _norm_unit(m.group("unit"))
            name = name[m.end():].strip()

    if amount is None:
        m = TRAILING_RE.search(name)
        if m and m.group("num") and name[:m.start()].strip():
            amount = _to_float(m.group("num"))
            unit = _norm_unit(m.group("unit"))
            name = name[:m.start()].strip()

    name = name.strip(" .,;:—–-")
    if not name:
        name = text.strip(" .,;:—–-")

    return {
        "raw": (raw or "").strip(),
        "name": name[:200],
        "name_norm": normalize_name(name),
        "amount": amount,
        "unit": unit[:40],
        "note": note[:200],
    }


def format_amount(amount: float | None, unit: str) -> str:
    if amount is None:
        return unit or ""
    if abs(amount - round(amount)) < 1e-6:
        num = str(int(round(amount)))
    else:
        num = f"{amount:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{num} {unit}".strip()
