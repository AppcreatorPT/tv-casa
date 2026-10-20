#!/usr/bin/env python3
"""Gera tv.m3u (lista arrumada) e epg.xml.gz (guia de TV).

Canais normais (iptv-org) ficam agrupados por país.
Canais temáticos grátis (Pluto TV) ficam agrupados por tema e língua.
Só entram canais estrangeiros que tenham guia de TV: é o melhor filtro de qualidade.

Só usa a biblioteca padrão do Python.
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import re
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_RAW = "https://raw.githubusercontent.com/AppcreatorPT/tv-casa/main"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
OUT = Path(__file__).parent

# Ordem dos grupos no TiviMate (também define a numeração dos canais).
GROUPS = [
    "🇵🇹 Portugal", "🇺🇸 EUA", "🇬🇧 Reino Unido", "🇫🇷 França",
    "🎬 Filmes · Português", "📺 Séries · Português",
    "🎬 Filmes · Inglês", "📺 Séries · Inglês",
    "🎬 Filmes · Francês", "📺 Séries · Francês",
    "🍥 Anime", "🧸 Infantil", "🌍 Documentários",
    "⚽ Desporto", "📰 Notícias", "🎵 Música",
]

# ---------- Canais normais por país (iptv-org) ----------
# Ordem dentro de cada país.
THEME_ORDER = ["General", "News", "Sports", "Movies", "Series", "Entertainment", "Comedy",
               "Documentary", "Lifestyle", "Kids", "Animation", "Family", "Music", "Undefined"]
KEEP_CATS = set(THEME_ORDER) | {"Culture", "Business", "Science", "Travel", "Cooking", "Outdoor", "Classic"}

COUNTRIES = [
    # (código iptv-org, grupo, ficheiros de guia epgshare01, exige guia?)
    ("pt", "🇵🇹 Portugal", ["PT1"], False),
    ("us", "🇺🇸 EUA", ["US2"], True),
    ("uk", "🇬🇧 Reino Unido", ["UK1"], True),
    ("fr", "🇫🇷 França", ["FR1"], True),
]
# Nomes a esconder: religiosos, compras, regionais, outras línguas, repetidos.
SKIP = re.compile(
    r"Mana |Kuriakos|Angel TV|Trailers|Radio Sines|ADB TV|KTO|MCM Pop|AXN White|Euronews Portuguese|FIFA\+ Portuguese"
    r"|Espa[nñ]ol|Deportes|Univision|Estrella|TUDN|De Pelicula|Tlnovelas|Mediaset|MBC America"
    r"|Aaj Tak|Brit Asia|Deen TV|Takbeer|Utsav|Zee |Nollywood|Africa 24|Trace (Africa|Caribbean|Latina)"
    r"|TBN|TCT|Positiv|Impact Network|AMG TV|Hobby Maker|TV Warehouse|Ion TV|TV One"
    r"|BFM (?!TV|Business)|France 3 \w|Cannes|TV78|ILTV|T18|Puissance|TV Monaco"
    r"|News12|\+1\b|Alba|S4C|BBC One West",
    re.I,
)


def fetch(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def norm(name):
    """'RTP 1 (720p) [Not 24/7]' e 'RTP.1.HD.pt' -> 'rtp1'."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\[.*?\]|\(.*?\)", " ", s)
    s = re.sub(r"\.(pt|us|uk|fr)$", "", s)
    s = re.sub(r"\b(hd|sd|fhd|uhd|4k|east|portugal|usa|us|uk|france)\b", " ", s)
    return re.sub(r"[^a-z0-9+]", "", s)


def parse_m3u(text):
    items, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            cur = {"attrs": attrs, "name": line.rsplit(",", 1)[-1].strip(), "opts": []}
        elif line.startswith("#EXTVLCOPT") and cur:
            cur["opts"].append(line)
        elif line and not line.startswith("#") and cur:
            cur["url"] = line
            items.append(cur)
            cur = None
    return items


def alive(ch):
    """Falso só quando o canal está mesmo morto. 403 conta como vivo (bloqueio fora do país)."""
    a = ch["attrs"]
    headers = {"User-Agent": a.get("http-user-agent") or UA}
    if a.get("http-referrer"):
        headers["Referer"] = a["http-referrer"]
    try:
        with urllib.request.urlopen(urllib.request.Request(ch["url"], headers=headers), timeout=12) as r:
            r.read(512)
        return True
    except urllib.error.HTTPError as e:
        return e.code in (401, 403, 451, 429)
    except Exception:
        return False


def clean_name(name):
    name = re.sub(r"\s*\[.*?\]", "", name)
    name = re.sub(r"\s*\((\d+p|Portugal|United States|United Kingdom|France)\)", "", name)
    return name.strip()


def load_epg(files):
    """Devolve (raízes XML, mapa nome-normalizado -> id do canal) dos ficheiros pedidos."""
    roots, ids = [], {}
    for f in files:
        root = ET.fromstring(fetch(f"https://epgshare01.online/epgshare01/epg_ripper_{f}.xml.gz"))
        roots.append(root)
        for c in root.findall("channel"):
            ids.setdefault(norm(c.get("id")), c.get("id"))
            for dn in c.findall("display-name"):
                ids.setdefault(norm(dn.text or ""), c.get("id"))
    return roots, ids


def country_channels(code, group, epg_ids, need_epg):
    items = parse_m3u(fetch(f"https://iptv-org.github.io/iptv/countries/{code}.m3u").decode())
    keep, seen = [], set()
    for ch in items:
        cats = ch["attrs"].get("group-title", "").split(";")
        if not KEEP_CATS.intersection(cats) or (need_epg and "Not 24/7" in ch["name"]):
            continue
        name = clean_name(ch["name"])
        if SKIP.search(ch["name"]) or name.endswith(" SD"):
            continue
        key = norm(name)
        epg = epg_ids.get(key, "")
        if key in seen or (need_epg and not epg):
            continue
        seen.add(key)
        theme = next((THEME_ORDER.index(c) for c in cats if c in THEME_ORDER), len(THEME_ORDER))
        ch.update(group=group, clean=name, epg=epg, order=theme)
        keep.append(ch)
    with cf.ThreadPoolExecutor(24) as ex:
        flags = list(ex.map(alive, keep))
    return [c for c, ok in zip(keep, flags) if ok]


# ---------- Pluto TV (canais temáticos grátis) ----------
PT_FILMES, PT_SERIES = "🎬 Filmes · Português", "📺 Séries · Português"
EN_FILMES, EN_SERIES = "🎬 Filmes · Inglês", "📺 Séries · Inglês"
FR_FILMES, FR_SERIES = "🎬 Filmes · Francês", "📺 Séries · Francês"
PLUTO = [
    # (região, IP da região, categoria -> grupo)
    ("BR", "177.47.27.205", {
        "Filmes": PT_FILMES, "Séries": PT_SERIES, "Comédia": PT_SERIES, "Novelas": PT_SERIES,
        "Star Trek": PT_SERIES, "South Park br": PT_SERIES, "Retrô": PT_SERIES, "Reality": PT_SERIES,
        "Investigação": PT_SERIES, "Mistérios e Sobrenatural": PT_SERIES, "TV Brasileira": PT_SERIES,
        "Anime & Geek": "🍥 Anime", "Infantil": "🧸 Infantil", "Nickelodeon": "🧸 Infantil",
        "Curiosidades": "🌍 Documentários", "Natureza": "🌍 Documentários", "Estilo de Vida": "🌍 Documentários",
        "Esportes": "⚽ Desporto", "Notícias": "📰 Notícias", "Música": "🎵 Música", "MTV": "🎵 Música",
    }),
    ("US", "108.82.206.181", {
        "Movies": EN_FILMES, "Westerns": EN_FILMES,
        "Drama": EN_SERIES, "Comedy": EN_SERIES, "Sci-Fi": EN_SERIES, "True Crime": EN_SERIES,
        "Classic TV": EN_SERIES, "Entertainment": EN_SERIES, "Competition Reality": EN_SERIES,
        "Anime": "🍥 Anime", "History + Science": "🌍 Documentários", "Animals + Nature": "🌍 Documentários",
        "Sports": "⚽ Desporto", "News + Opinion": "📰 Notícias",
    }),
    ("GB", "81.2.69.142", {
        "Movies": EN_FILMES, "Crime Drama": EN_SERIES, "Bingeable Drama": EN_SERIES,
        "Sci-Fi & Fantasy": EN_SERIES, "Comedy": EN_SERIES, "Classic TV": EN_SERIES, "True Crime": EN_SERIES,
        "Documentaries": "🌍 Documentários", "Real Life Adventure": "🌍 Documentários",
        "News": "📰 Notícias", "Sports": "⚽ Desporto",
    }),
    ("FR", "82.64.10.1", {
        "Cinéma": FR_FILMES, "Séries Policières": FR_SERIES, "À Binge Watch": FR_SERIES,
        "Crime & Mystère": FR_SERIES, "Comédie": FR_SERIES, "Emissions cultes": FR_SERIES,
        "100% Français": FR_SERIES, "Divertissement": FR_SERIES,
        "Documentaire": "🌍 Documentários", "News": "📰 Notícias",
    }),
]


def pluto():
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    channels, seen_names = {}, set()
    for region, ip, groups in PLUTO:
        region_channels = {}
        # A API devolve no máximo ~8 h por pedido; pedimos 3 janelas (24 h).
        for i in range(3):
            start = now + dt.timedelta(hours=8 * i - (1 if i == 0 else 0))
            stop = now + dt.timedelta(hours=8 * (i + 1))
            url = ("https://api.pluto.tv/v2/channels?start=" + start.strftime("%Y-%m-%dT%H:%M:%SZ")
                   + "&stop=" + stop.strftime("%Y-%m-%dT%H:%M:%SZ"))
            # O cabeçalho escolhe a região (e a língua do conteúdo).
            for ch in json.loads(fetch(url, {"X-Forwarded-For": ip})):
                if not ch.get("isStitched") or ch.get("category") not in groups:
                    continue
                prev = region_channels.get(ch["_id"])
                if prev:
                    prev["timelines"] += ch.get("timelines", [])
                else:
                    ch["group"] = groups[ch["category"]]
                    ch["clean"] = re.sub(r"^Pluto TV\s+|\s+(BR|UK|FR)$", "", ch["name"].strip())
                    region_channels[ch["_id"]] = ch
        # O mesmo canal pode existir em várias regiões: fica a primeira.
        for cid, ch in region_channels.items():
            key = (ch["group"], norm(ch["clean"]))
            if cid in channels or key in seen_names:
                continue
            seen_names.add(key)
            channels[cid] = ch
    return list(channels.values())


def xmltv_time(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y%m%d%H%M%S +0000")


# ---------- Saída ----------

def main():
    entries = []
    epg_roots = []
    for code, group, files, need_epg in COUNTRIES:
        roots, ids = load_epg(files)
        epg_roots += roots
        for c in country_channels(code, group, ids, need_epg):
            a = c["attrs"]
            opts = list(c["opts"])
            if a.get("http-user-agent"):
                opts.append(f'#EXTVLCOPT:http-user-agent={a["http-user-agent"]}')
            if a.get("http-referrer"):
                opts.append(f'#EXTVLCOPT:http-referrer={a["http-referrer"]}')
            entries.append(dict(group=group, name=c["clean"], url=c["url"], tvg=c["epg"],
                                logo=a.get("tvg-logo", ""), opts=opts, order=c["order"]))

    pl = pluto()
    for c in pl:
        logo = (c.get("colorLogoPNG") or c.get("logo") or {}).get("path", "")
        entries.append(dict(group=c["group"], name=c["clean"], url=f'https://jmp2.uk/plu-{c["_id"]}.m3u8',
                            tvg=f'pluto.{c["_id"]}', logo=logo, opts=[], order=0))

    entries.sort(key=lambda e: (GROUPS.index(e["group"]), e["order"], e["name"].lower()))

    lines = [f'#EXTM3U url-tvg="{REPO_RAW}/epg.xml.gz" x-tvg-url="{REPO_RAW}/epg.xml.gz"']
    for n, e in enumerate(entries, 1):
        name = e["name"].replace(",", " ")
        lines.append(f'#EXTINF:-1 tvg-chno="{n}" tvg-id="{e["tvg"]}" tvg-name="{name}" '
                     f'tvg-logo="{e["logo"]}" group-title="{e["group"]}",{name}')
        lines += list(dict.fromkeys(e["opts"]))
        lines.append(e["url"])
    (OUT / "tv.m3u").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Guia: só os canais usados.
    wanted = {e["tvg"] for e in entries if e["tvg"] and not e["tvg"].startswith("pluto.")}
    tv = ET.Element("tv", {"generator-info-name": "tv-casa"})
    added = set()
    for root in epg_roots:
        for c in root.findall("channel"):
            if c.get("id") in wanted and c.get("id") not in added:
                added.add(c.get("id"))
                tv.append(c)
    for c in pl:
        el = ET.SubElement(tv, "channel", id=f'pluto.{c["_id"]}')
        ET.SubElement(el, "display-name").text = c["clean"]
    for root in epg_roots:
        for p in root.findall("programme"):
            if p.get("channel") in wanted:
                tv.append(p)
    for c in pl:
        seen = set()
        for t in sorted(c.get("timelines", []), key=lambda t: t["start"]):
            if t["start"] in seen:
                continue
            seen.add(t["start"])
            p = ET.SubElement(tv, "programme", start=xmltv_time(t["start"]), stop=xmltv_time(t["stop"]),
                              channel=f'pluto.{c["_id"]}')
            ET.SubElement(p, "title").text = t.get("title", "")
            ep = t.get("episode") or {}
            if ep.get("name") and ep["name"] != t.get("title"):
                ET.SubElement(p, "sub-title").text = ep["name"]
            if ep.get("description"):
                ET.SubElement(p, "desc").text = ep["description"]
            if ep.get("genre"):
                ET.SubElement(p, "category").text = ep["genre"]
            poster = (ep.get("poster") or {}).get("path")
            if poster:
                ET.SubElement(p, "icon", src=poster)
            season, number = ep.get("season") or 0, ep.get("number") or 0
            if season and number and (season, number) != (1, 1):  # 1x1 é o valor por defeito dos filmes
                ET.SubElement(p, "episode-num", system="onscreen").text = f"T{season} E{number}"
    with gzip.open(OUT / "epg.xml.gz", "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="utf-8"))

    print(f"Canais: {len(entries)} (com guia: {sum(1 for e in entries if e['tvg'])})")
    for g in GROUPS:
        print(f"  {g}: {sum(1 for e in entries if e['group'] == g)}")


if __name__ == "__main__":
    main()
