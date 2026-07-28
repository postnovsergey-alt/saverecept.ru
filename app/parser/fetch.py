"""Загрузка страницы. Отдельный модуль, чтобы в тестах его было легко подменить."""
import httpx

from app.config import USER_AGENT

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


class FetchError(RuntimeError):
    pass


def fetch_html(url: str, timeout: float = 20.0) -> tuple[str, str]:
    """Возвращает (html, финальный_url после редиректов)."""
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=HEADERS) as client:
            r = client.get(url)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "xml" not in ctype:
                raise FetchError(f"По ссылке не страница, а {ctype or 'непонятно что'}")
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding or "utf-8"
            return r.text, str(r.url)
    except httpx.HTTPStatusError as e:
        raise FetchError(f"Сайт ответил {e.response.status_code}") from e
    except httpx.HTTPError as e:
        raise FetchError(f"Не удалось открыть ссылку: {e.__class__.__name__}") from e


def fetch_bytes(url: str, timeout: float = 20.0, max_bytes: int = 12_000_000) -> bytes:
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=HEADERS) as client:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            chunks, total = [], 0
            for chunk in r.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError("Картинка слишком большая")
                chunks.append(chunk)
            return b"".join(chunks)
