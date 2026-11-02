"""Book Scout: trova libri di successo negli USA non ancora pubblicati in Italia.

Fonti: classifiche Amazon.com, liste NYT Best Sellers.
Verifica Italia: Google Books (lingua it) + Open Library.
Output: data.json (letto da index.html) e uno snapshot settimanale in data/history/.
"""
import datetime as dt
import json
import math
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
HISTORY_DIR = ROOT / "data" / "history"
CACHE_FILE = ROOT / "data" / "cache.json"
OUT_FILE = ROOT / "data.json"

NYT_KEY = os.environ.get("NYT_API_KEY", "").strip()
GB_KEY = os.environ.get("GOOGLE_BOOKS_KEY", "").strip()
GB_BUDGET = 900          # chiamate Google Books massime per esecuzione
PRODUCT_PAGE_BUDGET = 80 # pagine prodotto Amazon (eta' di lettura) per esecuzione
AUTHOR_RECHECK_DAYS = 30

TODAY = dt.date.today()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Categorie Amazon.com (browse node) raggruppate per area editoriale
AMAZON_CATEGORIES = {
    "Bambini": {"Children's Books": "4"},
    "Manualistica e varia": {
        "Self-Help": "4736",
        "Crafts, Hobbies & Home": "48",
        "Cookbooks, Food & Wine": "6",
        "Health, Fitness & Dieting": "10",
        "Parenting & Relationships": "20",
    },
    "Saggistica": {
        "Business & Money": "3",
        "Science & Math": "75",
        "History": "9",
        "Biographies & Memoirs": "2",
        "Politics & Social Sciences": "3377866011",
    },
}
AMAZON_LISTS = {"bestsellers": "Best Sellers", "new-releases": "Novità"}

NYT_LISTS = {
    "picture-books": ("Bambini", "NYT Picture Books"),
    "childrens-middle-grade-hardcover": ("Bambini", "NYT Middle Grade"),
    "series-books": ("Bambini", "NYT Series"),
    "advice-how-to-and-miscellaneous": ("Manualistica e varia", "NYT Advice & How-To"),
    "hardcover-nonfiction": ("Saggistica", "NYT Hardcover Nonfiction"),
    "paperback-nonfiction": ("Saggistica", "NYT Paperback Nonfiction"),
}

FORMATS = {"hardcover", "paperback", "board book", "kindle", "kindle edition",
           "audible audiobook", "mass market paperback", "spiral-bound",
           "library binding", "audio cd", "cards", "flexibound", "unknown binding"}

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
status = {"amazon_ok": 0, "amazon_blocked": 0, "nyt": "non configurato",
          "google_calls": 0, "google_errors": 0, "product_pages": 0}


# ---------------------------------------------------------------- utilità

def log(*a):
    print(*a, flush=True)


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def short_title(t):
    """Titolo senza sottotitolo e senza indicazioni di serie tra parentesi."""
    t = re.split(r"[:(\[]", t or "")[0]
    return t.strip()


def first_author(a):
    a = re.split(r",| and | & |;", a or "")[0]
    return re.sub(r"^(by|illustrated by)\s+", "", a.strip(), flags=re.I)


def isbn10_to_13(i10):
    core = "978" + i10[:9]
    total = sum(int(d) * (1 if k % 2 == 0 else 3) for k, d in enumerate(core))
    return core + str((10 - total % 10) % 10)


def parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def get(url, **kw):
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            return r
        except requests.RequestException as e:
            log("  errore rete:", e)
            time.sleep(3)
    return None


# ---------------------------------------------------------------- raccolta

class Collector:
    def __init__(self):
        self.items = {}
        self.alias = {}

    def key_for(self, isbn13, asin, title, author):
        tkey = "t:" + " ".join(norm(short_title(title))) + "|" + " ".join(norm(first_author(author))[-1:])
        for k in (isbn13 and "i:" + isbn13, asin and "a:" + asin, tkey):
            if k and k in self.alias:
                return self.alias[k]
        main = (isbn13 and "i:" + isbn13) or (asin and "a:" + asin) or tkey
        for k in (isbn13 and "i:" + isbn13, asin and "a:" + asin, tkey):
            if k:
                self.alias[k] = main
        return main

    def add(self, *, title, author, macro, list_name, rank, source,
            isbn13=None, asin=None, **extra):
        if not title:
            return
        key = self.key_for(isbn13, asin, title, author)
        it = self.items.setdefault(key, {
            "key": key, "title": title, "author": author or "", "macro": [],
            "appearances": [], "isbn13": isbn13, "asin": asin,
        })
        if macro not in it["macro"]:
            it["macro"].append(macro)
        it["appearances"].append({"source": source, "list": list_name, "rank": rank})
        it["isbn13"] = it["isbn13"] or isbn13
        it["asin"] = it["asin"] or asin
        if not it["author"] and author:
            it["author"] = author
        for k, v in extra.items():
            if v not in (None, "", 0) and not it.get(k):
                it[k] = v


def is_blocked(html):
    return "captcha" in html.lower() or "api-services-support@amazon.com" in html


def parse_amazon_grid(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for root in soup.select("div#gridItemRoot"):
        node = root.select_one("[data-asin]")
        asin = node["data-asin"] if node else None
        badge = root.select_one(".zg-bdg-text")
        rank = int(re.sub(r"\D", "", badge.get_text())) if badge else None
        img = root.select_one("img")
        title = img.get("alt", "").strip() if img else ""
        if not title:
            first = root.select_one("[class*='line-clamp']")
            title = first.get_text(strip=True) if first else ""
        author = None
        for row in root.select("div.a-row.a-size-small"):
            txt = row.get_text(" ", strip=True)
            if txt and txt.lower() not in FORMATS:
                author = txt
                break
        fmt = root.select_one("span.a-color-secondary")
        rating = reviews = None
        star = root.select_one("a[aria-label*='out of 5 stars']")
        if star:
            m = re.search(r"([\d.]+) out of 5 stars, ([\d,]+)", star["aria-label"])
            if m:
                rating, reviews = float(m.group(1)), int(m.group(2).replace(",", ""))
        if asin and rank:
            out.append({"asin": asin, "rank": rank, "title": title, "author": author,
                        "format": fmt.get_text(strip=True) if fmt else None,
                        "rating": rating, "reviews": reviews,
                        "image": img.get("src") if img else None})
    return out


def collect_amazon(col):
    for macro, cats in AMAZON_CATEGORIES.items():
        for cat_name, node in cats.items():
            for list_slug, list_label in AMAZON_LISTS.items():
                url = f"https://www.amazon.com/gp/{list_slug}/books/{node}"
                r = get(url)
                time.sleep(2)
                if r is None or r.status_code != 200 or is_blocked(r.text):
                    status["amazon_blocked"] += 1
                    log(f"  Amazon BLOCCATO/errore: {url} ({r.status_code if r is not None else 'nessuna risposta'})")
                    continue
                rows = parse_amazon_grid(r.text)
                status["amazon_ok"] += 1
                log(f"  Amazon {cat_name} / {list_label}: {len(rows)} titoli")
                for row in rows:
                    asin = row.pop("asin")
                    isbn13 = isbn10_to_13(asin) if re.fullmatch(r"\d{9}[\dX]", asin) else None
                    col.add(title=row.pop("title"), author=row.pop("author"), macro=macro,
                            list_name=f"Amazon {list_label} · {cat_name}",
                            rank=row.pop("rank"), source="amazon", isbn13=isbn13, asin=asin,
                            amazon_url=f"https://www.amazon.com/dp/{asin}", **row)


def collect_nyt(col):
    if not NYT_KEY:
        log("  NYT: chiave assente, salto")
        return
    ok = 0
    for slug, (macro, label) in NYT_LISTS.items():
        r = get(f"https://api.nytimes.com/svc/books/v3/lists/current/{slug}.json",
                params={"api-key": NYT_KEY})
        time.sleep(13)  # limite NYT: 5 richieste al minuto
        if r is None or r.status_code != 200:
            log(f"  NYT {slug}: errore {r.status_code if r is not None else 'rete'}")
            continue
        ok += 1
        books = r.json().get("results", {}).get("books", [])
        log(f"  NYT {label}: {len(books)} titoli")
        for b in books:
            col.add(title=b["title"].title(), author=b.get("author"), macro=macro,
                    list_name=label, rank=b["rank"], source="nyt",
                    isbn13=b.get("primary_isbn13") or None,
                    image=b.get("book_image"), publisher_us=b.get("publisher"),
                    description=b.get("description"),
                    amazon_url=b.get("amazon_product_url"),
                    nyt_weeks=b.get("weeks_on_list"),
                    nyt_new=b.get("rank_last_week") == 0)
    status["nyt"] = f"{ok}/{len(NYT_LISTS)} liste lette"


# ---------------------------------------------------------------- arricchimento

google_fail_streak = 0


def google_books(q, **params):
    global google_fail_streak
    # dopo 8 errori consecutivi (quota esaurita o chiave non valida) si smette di insistere
    if status["google_calls"] >= GB_BUDGET or google_fail_streak >= 8:
        return None
    params = {"q": q, "maxResults": 40, "printType": "books", **params}
    if GB_KEY:
        params["key"] = GB_KEY
    status["google_calls"] += 1
    r = None
    for wait in (0, 3, 10, 30):  # il 429 di Google e' spesso un limite al minuto: si riprova
        time.sleep(wait or 0.4)
        try:
            r = session.get("https://www.googleapis.com/books/v1/volumes", params=params, timeout=30)
        except requests.RequestException:
            r = None
        if r is None or r.status_code != 429:
            break
    if r is None or r.status_code != 200:
        status["google_errors"] += 1
        google_fail_streak += 1
        if status["google_errors"] <= 3:
            log(f"  Google Books errore {r.status_code if r is not None else 'rete'}: "
                f"{r.text[:300] if r is not None else ''}")
        if google_fail_streak == 8:
            log("  Google Books non disponibile (quota o chiave): uso solo Open Library")
        return None
    google_fail_streak = 0
    return r.json().get("items", [])


def enrich_metadata(it, cache):
    """Data di pubblicazione, editore e descrizione dall'ISBN originale."""
    if not it.get("isbn13"):
        return
    meta = cache["meta"].get(it["isbn13"])
    if meta is None:
        res = google_books("isbn:" + it["isbn13"])
        if res is None:
            return
        info = res[0]["volumeInfo"] if res else {}
        meta = {"pub_date": info.get("publishedDate"), "publisher_us": info.get("publisher"),
                "description": (info.get("description") or "")[:400],
                "image": (info.get("imageLinks") or {}).get("thumbnail")}
        cache["meta"][it["isbn13"]] = meta
    for k, v in meta.items():
        if v and not it.get(k):
            it[k] = v


AGE_RE = re.compile(r"(Baby|\d+)\s*(?:months?|years?)?\s*-\s*(\d+)\s*years|(\d+)\s*years?\s*and\s*up", re.I)
DATE_RE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")


def enrich_children(it, cache):
    """Eta' di lettura e data di uscita dalla pagina prodotto Amazon (solo bambini)."""
    asin = it.get("asin")
    if not asin or "Bambini" not in it["macro"]:
        return
    page = cache["product"].get(asin)
    if page is None:
        if status["product_pages"] >= PRODUCT_PAGE_BUDGET:
            return
        status["product_pages"] += 1
        r = get(f"https://www.amazon.com/dp/{asin}")
        time.sleep(2)
        if r is None or r.status_code != 200 or is_blocked(r.text):
            return
        html = r.text
        page = {}
        i = html.find("Reading age")
        if i > 0:
            m = AGE_RE.search(BeautifulSoup(html[i:i + 600], "html.parser").get_text(" "))
            if m:
                lo = 0 if (m.group(1) or "").lower() == "baby" else int(m.group(1) or m.group(3))
                page["age_min"] = lo
                page["reading_age"] = m.group(0).strip()
        i = html.find("Publication date")
        if i > 0:
            m = DATE_RE.search(BeautifulSoup(html[i:i + 600], "html.parser").get_text(" "))
            if m:
                page["pub_date_amz"] = m.group(1)
        cache["product"][asin] = page
    it.update({k: v for k, v in page.items() if v is not None})
    if not it.get("pub_date") and page.get("pub_date_amz"):
        d = parse_date(page["pub_date_amz"])
        it["pub_date"] = d.isoformat() if d else None


def italian_author_check(author, cache):
    """Libri dell'autore gia' usciti in italiano (titoli ed editori)."""
    name = first_author(author)
    if not name or name.lower() in {"various", "various authors", "anonymous", "unknown"}:
        return None
    key = " ".join(norm(name))
    cached = cache["authors"].get(key)
    if cached and (TODAY - dt.date.fromisoformat(cached["checked"])).days < AUTHOR_RECHECK_DAYS:
        return cached
    surname = norm(name)[-1] if norm(name) else ""
    titles, publishers, ok = [], [], False

    res = google_books(f'inauthor:"{name}"', langRestrict="it")
    if res is not None:
        ok = True
        for v in res:
            info = v.get("volumeInfo", {})
            authors = " ".join(info.get("authors", [])).lower()
            if info.get("language") == "it" and surname in norm(authors):
                titles.append(info.get("title", ""))
                if info.get("publisher"):
                    publishers.append(info["publisher"])

    r = get("https://openlibrary.org/search.json",
            params={"author": name, "language": "ita", "fields": "title,publisher", "limit": 20})
    if r is not None and r.status_code == 200:
        docs = r.json().get("docs", [])
        # gli editori di Open Library mescolano tutte le edizioni (anche non italiane): si usano solo i titoli
        titles += [d.get("title", "") for d in docs]
        # Open Library da sola e' incompleta: un risultato vuoto non basta a dire "inedito"
        ok = ok or bool(docs)

    result = {"checked": TODAY.isoformat(), "ok": ok,
              "titles": sorted(set(t for t in titles if t))[:15],
              "publishers": sorted(set(publishers))[:8]}
    if ok:
        cache["authors"][key] = result
    return result


def italian_title_check(it, author_info, cache):
    """Cerca un'edizione italiana dello specifico titolo."""
    st = short_title(it["title"])
    words = [w for w in norm(st) if len(w) > 3]
    # 1) titolo originale mantenuto o parole distintive (nomi, serie) nei titoli italiani
    for t in author_info["titles"]:
        tw = set(norm(t))
        if words and sum(w in tw for w in words) >= max(1, math.ceil(len(words) * 0.6)):
            return t
    # 2) ricerca mirata su Google Books
    ck = it["key"]
    if ck in cache["title_hits"]:
        return cache["title_hits"][ck]
    surname = (norm(first_author(it["author"])) or [""])[-1]
    res = google_books(f'intitle:"{st}" inauthor:"{surname}"', langRestrict="it")
    if res is None:
        return None
    hit = next((v["volumeInfo"].get("title") for v in res
                if v.get("volumeInfo", {}).get("language") == "it"), "")
    cache["title_hits"][ck] = hit
    return hit


def italy_status(it, cache):
    info = italian_author_check(it["author"], cache)
    if not info or not info["ok"]:
        return {"status": "da_verificare", "titles": [], "publishers": []}
    base = {"titles": info["titles"][:6], "publishers": info["publishers"]}
    if not info["titles"]:
        return {"status": "autore_inedito", **base}
    hit = italian_title_check(it, info, cache)
    if hit:
        return {"status": "tradotto", "match": hit, **base}
    if hit is None:
        return {"status": "da_verificare", **base}
    return {"status": "autore_tradotto", **base}


# ---------------------------------------------------------------- punteggio

def load_history():
    snaps = sorted(HISTORY_DIR.glob("*.json"))
    return [(p.stem, load_json(p, {})) for p in snaps if p.stem != TODAY.isoformat()]


def score(it, history):
    reasons = []
    best = min(a["rank"] for a in it["appearances"])
    it["best_rank"] = best
    s_rank = max(0, (51 - min(best, 50)) / 50) * 25
    reasons.append(f"#{best} in {sorted(it['appearances'], key=lambda a: a['rank'])[0]['list']}")

    weeks_seen = sum(1 for _, snap in history if it["key"] in snap) + 1
    weeks = max(weeks_seen, it.get("nyt_weeks") or 0)
    it["weeks"] = weeks
    s_long = min(weeks, 20) / 20 * 25
    if weeks >= 3:
        reasons.append(f"{weeks} settimane in classifica")

    s_growth = 0
    prev = history[-1][1].get(it["key"]) if history else None
    if prev:
        delta = prev["best_rank"] - best
        it["delta"] = delta
        if delta > 0:
            s_growth = min(delta / 25, 1) * 20
            reasons.append(f"in salita di {delta} posizioni")
    elif it.get("nyt_new") and (it.get("nyt_weeks") or 0) <= 1:
        s_growth, it["new_entry"] = 15, True
        reasons.append("nuovo ingresso NYT")
    elif history:
        s_growth, it["new_entry"] = 12, True
        reasons.append("nuovo ingresso in classifica")

    s_new = 0
    pub = parse_date(it.get("pub_date") or "")
    if pub:
        age = (TODAY - pub).days
        it["days_since_pub"] = age
        if age < 0:
            s_new = 15
            reasons.append("in uscita prossimamente")
        elif age <= 180:
            s_new = 15
            reasons.append(f"uscito da {max(1, age // 30)} mesi")
        elif age <= 365:
            s_new = 8

    s_rev = 0
    if it.get("reviews"):
        s_rev = min(math.log10(it["reviews"]) / 4, 1) * 15 * ((it.get("rating") or 4) / 5)
        if it["reviews"] >= 500:
            reasons.append(f"{it['rating']}★ su {it['reviews']:,} recensioni".replace(",", "."))

    raw = s_rank + s_long + s_growth + s_new + s_rev
    mult = {"autore_inedito": 1.0, "autore_tradotto": 0.85,
            "da_verificare": 0.8, "tradotto": 0.2}[it["italy"]["status"]]
    it["score"] = round(raw * mult)
    it["score_parts"] = {"classifica": round(s_rank), "longevita": round(s_long),
                         "crescita": round(s_growth), "novita": round(s_new),
                         "recensioni": round(s_rev)}
    it["reasons"] = reasons


# ---------------------------------------------------------------- main

def main():
    cache = load_json(CACHE_FILE, {})
    for k in ("meta", "product", "authors", "title_hits"):
        cache.setdefault(k, {})
    # le ricerche di titolo vanno ripetute ogni volta: i libri vengono tradotti nel tempo
    cache["title_hits"] = {}
    # le verifiche autore fatte senza Google Books (versione 1) non sono affidabili
    if cache.get("version") != 2:
        cache["authors"], cache["version"] = {}, 2

    col = Collector()
    log("== Amazon.com")
    collect_amazon(col)
    log("== NYT")
    collect_nyt(col)
    items = list(col.items.values())
    log(f"== {len(items)} titoli unici. Arricchimento e verifica Italia...")

    for n, it in enumerate(items, 1):
        enrich_metadata(it, cache)
        enrich_children(it, cache)
        it["italy"] = italy_status(it, cache)
        if n % 50 == 0:
            log(f"  {n}/{len(items)} (Google: {status['google_calls']} chiamate)")

    history = load_history()
    for it in items:
        score(it, history)
    items.sort(key=lambda i: -i["score"])

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snap = {it["key"]: {"best_rank": it["best_rank"], "title": it["title"]} for it in items}
    (HISTORY_DIR / f"{TODAY.isoformat()}.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    OUT_FILE.write_text(json.dumps({
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "weeks_of_history": len(history) + 1,
        "status": status,
        "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log("== Fatto:", json.dumps(status))
    if not items:
        sys.exit("Nessun titolo raccolto: tutte le fonti hanno fallito")


if __name__ == "__main__":
    main()
