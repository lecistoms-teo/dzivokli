#!/usr/bin/env python3
"""
Dzīvokļu sludinājumu uzraugs: ss.com + city24.lv -> Telegram.

Katrā palaišanā:
  1. nolasa jaunākos sludinājumus no ss.com un city24.lv,
  2. pārbauda tos pret filtriem failā config.yaml,
  3. par katru jaunu atbilstošu sludinājumu nosūta ziņu Telegram,
  4. saglabā redzēto sludinājumu ID failā seen.json.

Pirmajā palaišanā (kad seen.json vēl nav) tiek nosūtīta apstiprinājuma ziņa
un līdz 3 piemēriem; pārējie esošie sludinājumi tikai tiek atzīmēti.

Vides mainīgie:
  TELEGRAM_BOT_TOKEN  – bota žetons no @BotFather
  TELEGRAM_CHAT_ID    – tavs čata ID (vai vairāki, atdalīti ar komatu)
  DRY_RUN=1           – nesūtīt ziņas, tikai izdrukāt konsolē
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
SEEN_PATH = ROOT / "seen.json"
SEEN_KEEP = 20000  # cik ID glabāt (vecākie tiek dzēsti)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "lv,en;q=0.8"})


# ---------------------------------------------------------------- dati

@dataclass
class Listing:
    source: str            # "ss" | "city24"
    id: str
    url: str
    city: str = ""
    district: str = ""
    street: str = ""
    rooms: int | None = None
    area: float | None = None
    floor: int | None = None
    floors_total: int | None = None
    series: str = ""
    price: float | None = None
    image: str | None = None
    title: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def price_m2(self) -> float | None:
        if self.price and self.area:
            return self.price / self.area
        return None


def norm(s: str | None) -> str:
    """Mazie burti bez garumzīmēm: 'Āgenskalns' -> 'agenskalns'."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def to_num(s: str | None) -> float | None:
    if s is None:
        return None
    s = str(s).replace("\xa0", "").replace(" ", "")
    m = re.search(r"\d[\d,\.]*", s)
    if not m:
        return None
    t = m.group(0)
    # ss.com: "110,700" = 110700; "52.5" = 52.5
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t):
        t = t.replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


# ---------------------------------------------------------------- ss.com

SS_BASE = "https://www.ss.com"


def ss_list_url(city: str, deal: str, page: int) -> str:
    deal_path = "sell" if deal == "sell" else "hand_over"
    p = "" if page == 1 else f"page{page}.html"
    return f"{SS_BASE}/lv/real-estate/flats/{city}/all/{deal_path}/{p}"


def parse_ss(html_text: str, city_label: str = "") -> list[Listing]:
    soup = BeautifulSoup(html_text, "html.parser")
    out: list[Listing] = []
    for tr in soup.select('tr[id^="tr_"]'):
        tr_id = tr.get("id", "")
        if not re.fullmatch(r"tr_\d+", tr_id):
            continue  # reklāmas u.c.
        a = tr.select_one("a.am") or tr.select_one('a[href*="/msg/"]')
        if not a:
            continue
        href = a.get("href", "")
        url = href if href.startswith("http") else SS_BASE + href
        cells = tr.select("td.msga2-o")
        if len(cells) < 6:
            continue
        # kolonnas: [rajons<br>iela] ist. m2 stāvs sērija cena
        loc = cells[0].get_text("\n", strip=True).split("\n")
        if len(loc) >= 2:
            district, street = loc[0], loc[-1]
        else:
            district, street = "", loc[0] if loc else ""
        rooms_txt = cells[1].get_text(strip=True)
        floor_txt = cells[3].get_text(strip=True)
        fm = re.match(r"(\d+)\s*/\s*(\d+)", floor_txt)
        img = tr.select_one("img.isfoto") or tr.select_one("img")
        img_src = img.get("src") if img else None
        if img_src and ".th2." in img_src:
            img_src = img_src.replace(".th2.", ".800.")
        out.append(
            Listing(
                source="ss",
                id=tr_id[3:],
                url=url,
                city=city_label,
                district=district,
                street=street,
                rooms=int(rooms_txt) if rooms_txt.isdigit() else None,
                area=to_num(cells[2].get_text(strip=True)),
                floor=int(fm.group(1)) if fm else None,
                floors_total=int(fm.group(2)) if fm else None,
                series=cells[4].get_text(strip=True),
                price=to_num(cells[-1].get_text(strip=True)),
                image=img_src if img_src and img_src.startswith("http") else None,
                title=a.get_text(" ", strip=True),
            )
        )
    return out


def fetch_ss(city: str, deal: str, pages: int, city_label: str) -> list[Listing]:
    res: list[Listing] = []
    for p in range(1, pages + 1):
        r = session.get(ss_list_url(city, deal, p), timeout=30)
        r.raise_for_status()
        r.encoding = "utf-8"
        res.extend(parse_ss(r.text, city_label))
        time.sleep(1.5)
    return res


# ---------------------------------------------------------------- city24

C24_API = "https://api.city24.lv/lv_LV/search/realties"


def parse_city24(items: list[dict]) -> list[Listing]:
    out = []
    for it in items:
        addr = it.get("address") or {}
        attrs = it.get("attributes") or {}
        fid = str(it.get("friendly_id") or it.get("id"))
        city = addr.get("city_name") or addr.get("parish_name") or ""
        street = " ".join(
            x for x in [addr.get("street_name"), addr.get("house_number")] if x
        )
        img = (it.get("main_image") or {}).get("url")
        if img:
            img = img.replace("{fmt:em}", "14")
        floor = attrs.get("FLOOR")
        out.append(
            Listing(
                source="city24",
                id=fid,
                url=f"https://www.city24.lv/real-estate/apartments-for-sale/x/{fid}",
                city=city,
                district=addr.get("district_name") or "",
                street=street,
                rooms=it.get("room_count"),
                area=to_num(it.get("property_size")),
                floor=floor if isinstance(floor, int) else None,
                floors_total=attrs.get("TOTAL_FLOORS")
                if isinstance(attrs.get("TOTAL_FLOORS"), int) else None,
                series=", ".join(attrs.get("HOUSE_TYPE") or []),
                price=to_num(it.get("price")),
                image=img,
                title="",
                extra={"year_built": it.get("year_built")},
            )
        )
    return out


def fetch_city24(deal: str, per_page: int) -> list[Listing]:
    params = {
        "address[cc]": 2,
        "tsType": "sale" if deal == "sell" else "rent",
        "unitType": "Apartment",
        "itemsPerPage": per_page,
        "page": 1,
        "order[datePublished]": "desc",
    }
    r = session.get(C24_API, params=params, timeout=30,
                    headers={"Accept": "application/json"})
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = data.get("hydra:member") or data.get("items") or []
    listings = parse_city24(data)
    if deal != "sell":
        for l in listings:
            l.url = l.url.replace("apartments-for-sale", "apartments-for-rent")
    return listings


# ---------------------------------------------------------------- filtri

def _in_range(v, lo, hi) -> bool:
    if lo is None and hi is None:
        return True
    if v is None:
        return False  # nezināma vērtība neatbilst, ja ir robeža
    if lo is not None and v < lo:
        return False
    if hi is not None and v > hi:
        return False
    return True


def matches(l: Listing, f: dict) -> bool:
    if f.get("sources") and l.source not in f["sources"]:
        return False
    if f.get("city") and l.city and norm(f["city"]) not in norm(l.city):
        return False
    d = norm(l.district)
    if f.get("districts"):
        if not any(norm(x) in d or (d and d in norm(x)) for x in f["districts"]):
            return False
    if f.get("exclude_districts"):
        if any(norm(x) in d for x in f["exclude_districts"]):
            return False
    if not _in_range(l.price, f.get("price_min"), f.get("price_max")):
        return False
    if not _in_range(l.area, f.get("area_min"), f.get("area_max")):
        return False
    if not _in_range(l.rooms, f.get("rooms_min"), f.get("rooms_max")):
        return False
    if not _in_range(l.price_m2, f.get("price_m2_min"), f.get("price_m2_max")):
        return False
    if not _in_range(l.floor, f.get("floor_min"), f.get("floor_max")):
        return False
    if f.get("not_last_floor") and l.floor and l.floors_total \
            and l.floor >= l.floors_total:
        return False
    if f.get("series"):
        if not any(norm(s) in norm(l.series) for s in f["series"]):
            return False
    if f.get("exclude_series"):
        if any(norm(s) in norm(l.series) for s in f["exclude_series"]):
            return False
    text = norm(f"{l.title} {l.street}")
    if f.get("keywords") and not any(norm(k) in text for k in f["keywords"]):
        return False
    if f.get("exclude_keywords") and any(norm(k) in text for k in f["exclude_keywords"]):
        return False
    return True


# ---------------------------------------------------------------- Telegram

def fmt_eur(v: float | None) -> str:
    return "?" if v is None else f"{v:,.0f} €".replace(",", " ")


def build_message(l: Listing, filter_names: list[str]) -> str:
    e = html.escape
    where = ", ".join(x for x in [l.street, l.district, l.city] if x)
    parts = []
    if l.rooms:
        parts.append(f"{l.rooms} ist.")
    if l.area:
        parts.append(f"{l.area:g} m²")
    if l.floor:
        parts.append(f"{l.floor}/{l.floors_total or '?'}. st.")
    if l.series:
        parts.append(l.series)
    pm2 = f" ({fmt_eur(l.price_m2)}/m²)" if l.price_m2 else ""
    src = "ss.com" if l.source == "ss" else "city24.lv"
    lines = [
        f"🆕 <b>{e(where or 'Jauns sludinājums')}</b>",
        e(" · ".join(parts)),
        f"💶 <b>{fmt_eur(l.price)}</b>{pm2}",
    ]
    if l.title:
        lines.append(f"<i>{e(l.title[:180])}</i>")
    lines.append(f"🔎 {e(', '.join(filter_names))} · {src}")
    lines.append(f'<a href="{e(l.url)}">Atvērt sludinājumu</a>')
    return "\n".join(lines)


def send_telegram(text: str, image: str | None) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chats = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    if os.environ.get("DRY_RUN") or not token or not chats:
        print("---- (netiek sūtīts) ----\n" + text + "\n")
        return
    for chat in chats:
        base = f"https://api.telegram.org/bot{token}"
        ok = False
        if image:
            r = session.post(f"{base}/sendPhoto", data={
                "chat_id": chat, "photo": image, "caption": text[:1024],
                "parse_mode": "HTML"}, timeout=30)
            ok = r.ok
        if not ok:
            r = session.post(f"{base}/sendMessage", data={
                "chat_id": chat, "text": text, "parse_mode": "HTML",
                "disable_web_page_preview": "false"}, timeout=30)
            if not r.ok:
                print("Telegram kļūda:", r.status_code, r.text[:300], file=sys.stderr)
        time.sleep(1.1)  # Telegram limits ~1 ziņa/sek uz čatu


# ---------------------------------------------------------------- galvenais

def load_seen() -> tuple[list[str], bool]:
    if SEEN_PATH.exists():
        return json.loads(SEEN_PATH.read_text("utf-8")), False
    return [], True


def main() -> int:
    cfg = yaml.safe_load(CONFIG_PATH.read_text("utf-8")) or {}
    filters = [f for f in (cfg.get("filters") or []) if f.get("enabled", True)]
    settings = cfg.get("settings") or {}
    deals = sorted({f.get("deal", "sell") for f in filters}) or ["sell"]
    ss_cities = settings.get("ss_cities") or {"riga": "Rīga"}

    listings: list[Listing] = []
    errors = []
    for deal in deals:
        for slug, label in ss_cities.items():
            try:
                got = fetch_ss(slug, deal, settings.get("ss_pages", 2), label)
                for l in got:
                    l.extra["deal"] = deal
                listings += got
                print(f"ss.com {slug}/{deal}: {len(got)}")
            except Exception as ex:  # noqa: BLE001
                errors.append(f"ss.com {slug}/{deal}: {ex}")
        try:
            got = fetch_city24(deal, settings.get("city24_items", 60))
            for l in got:
                l.extra["deal"] = deal
            listings += got
            print(f"city24 {deal}: {len(got)}")
        except Exception as ex:  # noqa: BLE001
            errors.append(f"city24/{deal}: {ex}")

    for e in errors:
        print("KĻŪDA:", e, file=sys.stderr)

    seen, first_run = load_seen()
    seen_set = set(seen)
    new = [l for l in listings if l.key not in seen_set]
    # izslēdz dublikātus vienas palaišanas ietvaros
    uniq: dict[str, Listing] = {}
    for l in new:
        uniq.setdefault(l.key, l)
    new = list(uniq.values())

    sent = 0
    if first_run:
        print(f"Pirmā palaišana: atzīmēti {len(new)} esošie sludinājumi.")
        matching = [l for l in new if any(
            f.get("deal", "sell") == l.extra.get("deal") and matches(l, f) for f in filters)]
        src_ok = sorted({("ss.com" if l.source == "ss" else "city24.lv") for l in listings})
        send_telegram(
            "✅ <b>Dzīvokļu uzraugs darbojas!</b>\n"
            f"Avoti: {html.escape(', '.join(src_ok) or 'neviens')}\n"
            f"Pārbaudīti {len(new)} sludinājumi, filtriem šobrīd atbilst {len(matching)}.\n"
            "No šī brīža saņemsi ziņu par katru jaunu atbilstošu sludinājumu. "
            "Zemāk daži pašreizējie piemēri:", None)
        for l in matching[:3]:
            names = [f.get("name", "Filtrs") for f in filters if matches(l, f)]
            send_telegram(build_message(l, names), l.image)
    else:
        for l in new:
            names = [f.get("name", "Filtrs") for f in filters
                     if f.get("deal", "sell") == l.extra.get("deal") and matches(l, f)]
            if names:
                send_telegram(build_message(l, names), l.image)
                sent += 1
        print(f"Jauni: {len(new)}, atbilst filtriem un nosūtīti: {sent}")

    seen = (seen + [l.key for l in new])[-SEEN_KEEP:]
    SEEN_PATH.write_text(json.dumps(seen, ensure_ascii=False), "utf-8")

    # ja abi avoti nestrādā, darbība beidzas ar kļūdu (redzams GitHub)
    return 1 if errors and not listings else 0


if __name__ == "__main__":
    sys.exit(main())
