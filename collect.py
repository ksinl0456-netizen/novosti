# -*- coding: utf-8 -*-
"""Сборщик новостей из публичных Telegram-каналов через веб-версию t.me/s/.
Запускается на GitHub Actions по расписанию. Телефон не участвует.
"""
import re, html, json, os, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"}

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "docs", "data")
CH_FILE = os.path.join(ROOT, "channels.txt")

KEEP_DAYS = 120      # сколько дней хранить
MAX_PAGES = 6        # глубина листания на канал
PAUSE = 1.2          # пауза между запросами
TRANSLATE = True     # переводить каналы с пометкой


# ---------- сеть ----------

def net(url, tries=4, timeout=45):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
            last = e
        except Exception as e:
            last = e
        time.sleep(min(3 * (i + 1), 10))
    print("    сеть не отвечает: " + str(last)[:80])
    return None



# ---------- очистка ----------

AGENCY = r"(?:ИНТЕРФАКС|РИА|ТАСС|Интерфакс|Коммерсантъ|Lenta\.ru|РБК|Рен-ТВ)"
MONTHS = r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)"

JUNK_TAIL = [
    r"\s*(?:Подробнее|Подробности|Читайте также|Смотрите также|Больше новостей|"
    r"Все новости|Источник|Полный текст)\s*[:—–-].*$",
    r"\s*(?:Подписывайтесь|Подпишись|Подписаться|Присоединяйтесь|Вступайте)"
    r"[^.\n]*\.?\s*$",
    r"\s*(?:Видео|Фото|Читайте|Смотрите)\s*:\s*[^.\n]*\.?\s*$",
    r"\s*https?://\S+\s*$",
    r"\s*t\.me/\S+\s*$",
    r"\s*@\w+\s*$",
    r"\s*[|↓⬇►▶✔️]+\s*$",
]


def strip_dateline(t):
    head = t[:120]
    m = re.match(r"^(.{3,90}?)\s*[—–]\s*", head, re.S)
    if m:
        pre = m.group(1)
        if (re.search(MONTHS, pre, re.I) or re.search(AGENCY, pre)
                or re.match(r"^[А-ЯЁ][А-ЯЁа-яё\-\s]{2,25},\s", pre)):
            t = head[m.end():] + t[120:]
    t = re.sub(r"^" + AGENCY + r"[\.,]?\s*[—–-]?\s*", "", t)
    t = re.sub(r"^[•—–\-\*\s]+", "", t)
    return t.strip()


def strip_symbols(t):
    while True:
        m = re.search(r"(?:^|\s)([^\w\s]+)\s*$", t)
        if not m:
            break
        t = t[:m.start()].rstrip(" \n\t—–-|")
    return t


def tidy(t):
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)
    t = re.sub(r"[\s.,;:—–-]+$", "", t)
    return t.strip()


def clean_text(t):
    t = strip_dateline(t)
    for _ in range(3):
        before = t
        for p in JUNK_TAIL:
            t = re.sub(p, "", t, flags=re.S | re.I).strip()
        if t == before:
            break
    lines = [strip_symbols(l) for l in t.split("\n")]
    t = "\n".join(lines).strip()
    return tidy(t)


# ---------- разбор ----------

def clean(s):
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def parse(page):
    out = []
    blocks = page.split("tgme_widget_message_wrap")[1:]
    for b in blocks:
        m = re.search(r'data-post="([^"]+)"', b)
        t = re.search(r'<time datetime="([^"]+)"', b)
        if not m or not t:
            continue
        tx = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', b, re.S)
        if not tx:
            continue
        text = clean_text(clean(tx.group(1)))
        if not text:
            continue
        out.append({"key": m.group(1), "date": t.group(1), "text": text})
    return out


def fetch_channel(user, days_back=3):
    """Возвращает посты за последние days_back дней. Листает назад через ?before="""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    got = {}
    url = "https://t.me/s/" + user
    for page_no in range(MAX_PAGES):
        raw = net(url)
        if raw is None:
            break
        posts = parse(raw.decode("utf-8", "ignore"))
        if not posts:
            break
        oldest = None
        for p in posts:
            got[p["key"]] = p
            ts = datetime.fromisoformat(p["date"])
            if oldest is None or ts < oldest:
                oldest = ts
        if oldest is not None and oldest <= cutoff:
            break
        nums = []
        for k in got:
            try:
                nums.append(int(k.split("/")[1]))
            except Exception:
                pass
        if not nums:
            break
        url = "https://t.me/s/" + user + "?before=" + str(min(nums))
        time.sleep(PAUSE)
    return got


# ---------- перевод ----------

def translate_text(text, tries=2):
    """Неофициальная бесплатная точка Google. Без ключа и аккаунта."""
    q = urllib.parse.quote(text[:1800])
    url = ("https://translate.googleapis.com/translate_a/single"
           "?client=gtx&sl=auto&tl=ru&dt=t&q=" + q)
    for i in range(tries):
        raw = net(url, tries=1, timeout=30)
        if raw is None:
            time.sleep(2)
            continue
        try:
            arr = json.loads(raw.decode("utf-8"))
            parts = [p[0] for p in arr[0] if p and p[0]]
            res = "".join(parts).strip()
            if res:
                return res
        except Exception:
            pass
        time.sleep(2)
    return None


# ---------- хранилище ----------

def load_day(day):
    p = os.path.join(DATA, day + ".json")
    if os.path.exists(p):
        try:
            data = json.load(open(p, encoding="utf-8"))
            if isinstance(data, list):
                return {x["id"]: x for x in data if isinstance(x, dict) and "id" in x}
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
    return {}


def save_day(day, store):
    os.makedirs(DATA, exist_ok=True)
    p = os.path.join(DATA, day + ".json")
    items = sorted(store.values(), key=lambda x: x["date"])
    json.dump(items, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))


def read_channels():
    out = []
    if not os.path.exists(CH_FILE):
        print("нет channels.txt")
        return out
    for raw in open(CH_FILE, encoding="utf-8"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        user = parts[0].lstrip("@").replace("https://t.me/", "").replace("t.me/", "").strip("/")
        if not user:
            continue
        name = parts[1] if len(parts) > 1 and parts[1] else user
        do_tr = len(parts) > 2 and parts[2].lower().startswith("trans")
        out.append({"user": user, "name": name, "translate": do_tr})
    return out


def cleanup():
    if not os.path.isdir(DATA):
        return
    limit = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).date()
    removed = 0
    for f in os.listdir(DATA):
        if not f.endswith(".json") or f == "index.json":
            continue
        try:
            d = datetime.strptime(f[:-5], "%Y-%m-%d").date()
        except Exception:
            continue
        if d < limit:
            os.remove(os.path.join(DATA, f))
            removed += 1
    if removed:
        print("удалено старых файлов: " + str(removed))


def build_index():
    days = []
    if os.path.isdir(DATA):
        for f in sorted(os.listdir(DATA), reverse=True):
            if f.endswith(".json") and f != "index.json":
                try:
                    n = len(json.load(open(os.path.join(DATA, f), encoding="utf-8")))
                except Exception:
                    n = 0
                days.append({"day": f[:-5], "count": n})
    json.dump({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "days": days},
              open(os.path.join(DATA, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print("index: " + str(len(days)) + " дней")


def main():
    chans = read_channels()
    print("каналов: " + str(len(chans)))
    total_new = 0
    for ch in chans:
        print("— " + ch["user"])
        posts = fetch_channel(ch["user"])
        added = 0
        for key, p in posts.items():
            day = datetime.fromisoformat(p["date"]).astimezone(timezone.utc).strftime("%Y-%m-%d")
            store = load_day(day)
            if key in store:
                continue
            text = p["text"]
            translated = False
            if ch["translate"] and TRANSLATE:
                ru = translate_text(text)
                if ru:
                    text = ru
                    translated = True
                else:
                    print("    перевод недоступен")
            store[key] = {
                "id": key,
                "ch": ch["user"],
                "name": ch["name"],
                "date": p["date"],
                "text": text,
                "tr": translated,
                "url": "https://t.me/" + key,
            }
            save_day(day, store)
            added += 1
        print("    новых: " + str(added))
        total_new += added
    cleanup()
    build_index()
    print("ИТОГО новых: " + str(total_new))


if __name__ == "__main__":
    main()
