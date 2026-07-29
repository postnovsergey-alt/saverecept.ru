import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "yclid", "_openstat", "from", "ref", "referrer",
}


def slugify(text: str, max_len: int = 80) -> str:
    text = (text or "").lower().strip()
    out = "".join(TRANSLIT.get(ch, ch) for ch in text)
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out[:max_len] or "recipe"


def normalize_url(url: str) -> str:
    """Убирает utm-хвосты и якоря — чтобы одна и та же ссылка не задваивалась."""
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in TRACKING_PARAMS]
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), netloc, path, "", urlencode(query), ""))


def url_key(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:32]


def normalize_text(text: str) -> str:
    """Нормализация свободного текста для дедупа: одна строка со схлопнутыми
    пробелами, нижний регистр, без «ё». Смысл — тот же копипаст не создавал
    дубли из-за случайных отступов/переносов."""
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def text_key(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:32]


def domain_of(url: str) -> str:
    netloc = urlparse(url or "").netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+")


def find_url(text: str) -> str | None:
    m = URL_IN_TEXT.search(text or "")
    return m.group(0).rstrip(".,;)") if m else None


def shorten(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# Видео-embed по URL источника: возвращает {"kind": ..., ...} или None.
# YouTube — обычный iframe (лёгкий, без внешних JS-скриптов).
# Instagram — только по клику подгружаем embed.js, чтобы Meta не следила за
# каждым визитом карточки. TikTok/остальные пока не поддерживаем — падаем в
# кнопку "Источник ↗".
_YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([\w-]{11})",
    re.I,
)
_INSTA_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/([\w-]+)",
    re.I,
)
_TIKTOK_RE = re.compile(
    r"^https?://(?:www\.|vm\.|vt\.)?tiktok\.com/",
    re.I,
)


def video_embed(url: str) -> dict | None:
    if not url or url.startswith("photo://"):
        return None
    m = _YT_RE.search(url)
    if m:
        return {"kind": "youtube", "video_id": m.group(1), "url": url}
    m = _INSTA_RE.search(url)
    if m:
        return {"kind": "instagram", "url": url}
    if _TIKTOK_RE.search(url):
        return {"kind": "tiktok", "url": url}
    return None
