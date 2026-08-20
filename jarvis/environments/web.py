"""Web environment — benchmark category 6 (web / information retrieval).

Search → fetch → extract → cite. No API key required: uses DuckDuckGo's HTML
endpoint and then reads the actual pages, so answers are grounded in real
retrieved text with sources the user can check, not in model recall alone.

Pages that require JavaScript to render will yield little text. That is a known
limitation of an HTTP-only fetcher; the Environment interface is what a
browser-driven implementation would later plug into unchanged.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from jarvis.config import settings
from jarvis.core.contracts import ActionResult, VerificationResult
from jarvis.core.events import emit

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_PAGE_CHARS = 12_000

# Primary search backend. DuckDuckGo's plain HTML endpoint now answers scripted
# requests with an anti-bot challenge (HTTP 202 and no results), so the
# maintained client library is used first and the raw endpoint is kept only as a
# fallback for the case where the library is missing or itself fails.
try:  # pragma: no cover - availability depends on the environment
    from ddgs import DDGS  # type: ignore
    _HAVE_DDGS = True
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS  # type: ignore
        _HAVE_DDGS = True
    except ImportError:
        _HAVE_DDGS = False


SEARCH_DEADLINE_S = 25


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _with_deadline(fn, seconds: int, *args):
    """Run `fn` on a worker thread and give up waiting after `seconds`.

    The search client has no timeout parameter and retries internally with
    backoff, so bounding it from the outside is the only option. The thread is
    left as a daemon: it cannot block interpreter exit, and its result is simply
    discarded.
    """
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = fn(*args)
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError(f"exceeded {seconds}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


# Wikipedia's index is keyword-based: asked "what does HTTP status 429 mean" it
# returns Israel and Fusion power, but "HTTP status 429" returns exactly the
# right pages. Question scaffolding is therefore stripped before querying.
_QUESTION_WORDS = {
    "what", "whats", "what's", "who", "whos", "who's", "when", "where", "why",
    "how", "which", "is", "are", "was", "were", "do", "does", "did", "the",
    "a", "an", "of", "to", "for", "in", "on", "me", "my", "tell", "explain",
    "mean", "means", "meaning", "about", "please", "can", "you", "search",
    "look", "up", "find", "give", "show",
}


def _keywords(query: str) -> str:
    """Reduce a natural-language question to its content words."""
    words = re.findall(r"[\w.+#-]+", query.lower())
    kept = [w for w in words if w not in _QUESTION_WORDS]
    # If stripping removed everything, the original was already keyword-like.
    return " ".join(kept) if kept else query



class WebEnvironment:
    """Search the web and read pages over plain HTTP."""

    id = "web"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        })

    # -- Environment protocol ---------------------------------------------

    def state(self) -> dict[str, Any]:
        return {"online": self.online(), "endpoint": SEARCH_ENDPOINT}

    def online(self) -> bool:
        try:
            self._session.head("https://duckduckgo.com", timeout=5)
            return True
        except requests.RequestException:
            return False

    def capabilities(self) -> list[str]:
        return ["web_search", "fetch_page"]

    def constraints(self) -> list[str]:
        return [
            "HTTP-only fetching: pages that render entirely via JavaScript "
            "return little usable text.",
            "Requires network connectivity; offline requests degrade to local "
            "knowledge with that limitation stated.",
        ]

    def act(self, ability_id: str, args: dict[str, Any]) -> ActionResult:
        handlers = {"web_search": self._search, "fetch_page": self._fetch}
        handler = handlers.get(ability_id)
        if handler is None:
            return ActionResult(ok=False, summary=f"unknown ability '{ability_id}'",
                                error="unregistered")
        start = time.perf_counter()
        try:
            result = handler(args)
        except requests.RequestException as exc:
            result = ActionResult(ok=False, summary=f"Network error: {exc}",
                                  error=str(exc))
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        return result

    def verify(self, ability_id: str, args: dict[str, Any],
               result: ActionResult) -> VerificationResult:
        if ability_id == "web_search":
            n = result.evidence.get("result_count", 0)
            return VerificationResult(
                verified=n > 0, strategy="results_returned",
                detail=f"{n} search result(s)", checked={"result_count": n})
        if ability_id == "fetch_page":
            chars = result.evidence.get("chars", 0)
            return VerificationResult(
                verified=chars > 200, strategy="page_text_extracted",
                detail=f"{chars} chars of readable text",
                checked={"chars": chars, "url": result.evidence.get("url")})
        return VerificationResult(verified=result.ok, strategy="result_only",
                                  detail="", checked={})

    # -- handlers ----------------------------------------------------------

    def _search(self, args: dict[str, Any]) -> ActionResult:
        query = str(args.get("query") or args.get("q") or "").strip()
        if not query:
            return ActionResult(ok=False, summary="No search query given",
                                error="missing_query")
        limit = int(args.get("limit", 5))
        emit("web.search", query=query)

        results: list[dict[str, str]] = []
        backend = "none"
        if _HAVE_DDGS:
            # Hard wall-clock bound. The search client retries internally with
            # backoff when the engine rate-limits, which has been observed to
            # stall a single query for several minutes - unacceptable in an
            # interactive agent, so it is abandoned and the fallback is tried.
            try:
                results = _with_deadline(self._search_ddgs, SEARCH_DEADLINE_S,
                                         query, limit)
                backend = "ddgs" if results else backend
            except TimeoutError:
                emit("web.search_backend_failed", backend="ddgs",
                     error=f"exceeded {SEARCH_DEADLINE_S}s deadline")
            except Exception as exc:  # noqa: BLE001 - fall through to raw HTTP
                emit("web.search_backend_failed", backend="ddgs", error=str(exc))

        if not results:
            results = self._search_html(query, limit)
            backend = "duckduckgo_html" if results else backend

        if not results:
            # A single engine rate-limiting must not take web retrieval down, so
            # a structurally independent backend is tried before giving up.
            results = self._search_wikipedia(query, limit)
            backend = "wikipedia" if results else backend

        return ActionResult(
            ok=bool(results),
            summary=f"{len(results)} result(s) for '{query}'"
                    + ("" if results else " (search backends returned nothing)"),
            evidence={"query": query, "result_count": len(results),
                      "results": results, "backend": backend})

    @staticmethod
    def _search_ddgs(query: str, limit: int) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        with DDGS() as client:
            for row in client.text(query, max_results=limit):
                href = row.get("href") or row.get("url") or ""
                if not href.startswith("http"):
                    continue
                rows.append({
                    "title": _clean(row.get("title", "")),
                    "url": href,
                    "snippet": _clean(row.get("body") or row.get("snippet") or ""),
                })
        return rows

    def _search_wikipedia(self, query: str, limit: int) -> list[dict[str, str]]:
        """Encyclopedia fallback for when the general engine is rate-limited.

        Chosen over scraping a second search engine: Bing's plain results page
        answers scripted requests with a bot-mitigated page that silently
        ignores most of the query (it returned "do vs does" articles for "what
        does HTTP status 429 mean"). A fallback that returns confidently wrong
        sources is worse than none, whereas Wikipedia's API is stable, keyless,
        and honest about finding nothing.

        It covers factual questions well and current events poorly, so the
        backend name is recorded and surfaced rather than hidden.
        """
        results: list[dict[str, str]] = []
        try:
            resp = self._session.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search",
                        "srsearch": _keywords(query),
                        "srlimit": limit, "format": "json"},
                timeout=settings.WEB_TIMEOUT_S,
            )
            resp.raise_for_status()
            hits = resp.json().get("query", {}).get("search", [])
        except (requests.RequestException, ValueError) as exc:
            emit("web.search_backend_failed", backend="wikipedia", error=str(exc))
            return results
        for hit in hits:
            title = hit.get("title", "")
            if not title:
                continue
            slug = title.replace(" ", "_")
            results.append({
                "title": title,
                "url": f"https://en.wikipedia.org/wiki/{slug}",
                # The API returns the snippet with HTML highlight markup.
                "snippet": _clean(BeautifulSoup(hit.get("snippet", ""),
                                                "html.parser").get_text()),
            })
        return results

    def _search_html(self, query: str, limit: int) -> list[dict[str, str]]:
        """Fallback: scrape the plain HTML endpoint directly."""
        results: list[dict[str, str]] = []
        try:
            resp = self._session.post(
                SEARCH_ENDPOINT, data={"q": query, "kl": "us-en"},
                timeout=settings.WEB_TIMEOUT_S,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            emit("web.search_backend_failed", backend="duckduckgo_html",
                 error=str(exc))
            return results
        soup = BeautifulSoup(resp.text, "html.parser")
        for anchor in soup.select("a.result__a"):
            href = anchor.get("href", "")
            # DuckDuckGo wraps outbound links in /l/?uddg=<encoded url>
            if "uddg=" in href:
                qs = parse_qs(urlparse(href).query)
                href = unquote(qs.get("uddg", [""])[0]) or href
            if not href.startswith("http"):
                continue
            container = anchor.find_parent(class_="result")
            snippet = ""
            if container is not None:
                snippet_el = container.select_one(".result__snippet")
                if snippet_el:
                    snippet = _clean(snippet_el.get_text())
            results.append({"title": _clean(anchor.get_text()), "url": href,
                            "snippet": snippet})
            if len(results) >= limit:
                break
        return results

    def _fetch(self, args: dict[str, Any]) -> ActionResult:
        url = str(args.get("url") or "").strip()
        if not url:
            return ActionResult(ok=False, summary="No URL given", error="missing_url")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        emit("web.fetch", url=url)
        try:
            resp = self._session.get(url, timeout=settings.WEB_TIMEOUT_S)
            resp.raise_for_status()
        except requests.RequestException as exc:
            # Rate limits and dead links are normal on the open web. Report the
            # page as unavailable so the caller can fall back to another source.
            emit("web.fetch_failed", url=url, error=str(exc)[:200])
            return ActionResult(
                ok=False, summary=f"Could not fetch {url}: {exc}",
                error="fetch_failed", evidence={"url": url})
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                         "noscript", "form"]):
            tag.decompose()
        title = _clean(soup.title.get_text()) if soup.title else url
        main = soup.find("article") or soup.find("main") or soup.body or soup
        text = _clean(main.get_text(separator=" "))
        truncated = len(text) > MAX_PAGE_CHARS
        return ActionResult(
            ok=len(text) > 0,
            summary=f"Fetched '{title}' ({len(text)} chars"
                    f"{', truncated' if truncated else ''})",
            evidence={"url": url, "title": title, "text": text[:MAX_PAGE_CHARS],
                      "chars": len(text), "truncated": truncated,
                      "status_code": resp.status_code,
                      "bytes_received": len(resp.content)})

    # -- convenience used by the research pipeline -------------------------

    def research(self, query: str, *, pages: int = 3) -> dict[str, Any]:
        """Search, then read the top pages. Returns evidence for synthesis."""
        search = self._search({"query": query, "limit": max(pages, 3)})
        sources: list[dict[str, Any]] = []
        if not search.ok:
            return {"query": query, "sources": sources, "search_ok": False}
        for item in search.evidence.get("results", [])[:pages]:
            try:
                page = self._fetch({"url": item["url"]})
            except Exception as exc:  # noqa: BLE001 - one bad page is not fatal
                emit("web.page_skipped", url=item["url"], error=str(exc)[:160])
                page = ActionResult(ok=False, summary="skipped", error="skipped")
            if page.ok and page.evidence.get("chars", 0) > 200:
                sources.append({
                    "title": page.evidence.get("title") or item["title"],
                    "url": item["url"],
                    "text": page.evidence["text"][:5000],
                })
            else:
                sources.append({"title": item["title"], "url": item["url"],
                                "text": item.get("snippet", "")})
        return {"query": query, "sources": sources, "search_ok": True,
                "search_results": search.evidence.get("results", [])}


def search_url(query: str) -> str:
    return f"https://duckduckgo.com/?q={quote_plus(query)}"
