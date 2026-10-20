#!/usr/bin/env python3
"""Gera tv.m3u (lista arrumada) e epg.xml.gz (guia de TV).

Canais normais (iptv-org) ficam agrupados por país.
Canais temáticos grátis (Pluto TV) ficam agrupados por tema e língua.
Só entram canais estrangeiros que tenham guia de TV: é o melhor filtro de qualidade.

Se uma fonte falhar, os canais dela vêm da lista anterior. Se a lista nova ficar
muito mais pequena do que a anterior, nada é escrito (a Action falha e avisa por e-mail).

Só usa a biblioteca padrão do Python.
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_RAW = "https://raw.githubusercontent.com/AppcreatorPT/tv-casa/main"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
OUT = Path(__file__).parent
NOW = dt.datetime.now(dt.timezone.utc)

# Grupos pela ordem do TiviMate, com o primeiro número de cada um.
# Os números são fixos por grupo: um canal que morre só mexe nos números do seu grupo.
GROUPS = [
    ("⭐ Favoritos", 1),
    ("🇵🇹 Portugal", 100), ("🇺🇸 EUA", 200), ("🇬🇧 Reino Unido", 400), ("🇫🇷 França", 500),
    ("🎬 Filmes · Português", 600), ("📺 Séries · Português", 700),
    ("🎬 Filmes · Inglês", 800), ("📺 Séries · Inglês", 900),
    ("🎬 Filmes · Francês", 1200), ("📺 Séries · Francês", 1300),
    ("🍥 Anime", 1400), ("🧸 Infantil", 1500), ("🌍 Documentários", 1600),
    ("⚽ Desporto", 1700), ("📰 Notícias", 1800), ("🎵 Música", 1900),
]
GROUP_NAMES = [g for g, _ in GROUPS]

# Favoritos: o número é a posição nesta lista (RTP 1 é sempre o 1). Os que não existirem ficam por usar.
FAVORITOS = [
    "RTP 1", "RTP 2", "SIC", "TVI", "CNN Portugal", "SIC Notícias", "RTP Notícias", "V+ TVI",
    "RTP Memória", "Porto Canal", "TVI Ficção", "Euronews Português",
    "CNN", "TNT", "TBS", "HBO", "BBC One", "BBC Two", "BBC Three", "Channel 4",
    "TF1", "France 2", "France 5", "M6", "arte", "BFM TV", "Naruto Shippuden", "One Piece",
]

# ---------- Canais normais por país (iptv-org) ----------
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
# Nomes a esconder: religiosos, compras, regionais, outras línguas.
SKIP = re.compile(
    r"Mana |Kuriakos|Angel TV|Trailers|Radio Sines|ADB TV|\bKTO\b|MCM Pop|AXN White"
    r"|Espa[nñ]ol|Deportes|Latino|Univision|Estrella|TUDN|De Pelicula|Tlnovelas|Mediaset|MBC America"
    r"|Aaj Tak|Brit Asia|Deen TV|Takbeer|Utsav|\bZee\b|Nollywood|Africa 24|Trace (Africa|Caribbean|Latina|Gospel)"
    r"|\bTBN\b|\bTCT\b|Positiv|Impact Network|AMG TV|Hobby Maker|TV Warehouse|\bTV One\b"
    r"|BFM (?!TV|Business)|France 3 \w|Cannes|\bTV78\b|\bILTV\b|\bT18\b|Puissance|TV Monaco"
    r"|News12|\+1\b|\bAlba\b|\bS4C\b|BBC One West\b",
    re.I,
)
# Guias cujo nome não bate certo com o da lista (nome normalizado -> id no guia).
EPG_ALIAS = {
    "bbcone": "BBC.One.Lon.HD.uk",
    "bbctwo": "BBC.Two.HD.uk",
    "ae": "A.and.E.HD.East.us2",
}
# Versões do guia com horário de outra costa ou repetidas: só se usam se não houver outra.
EPG_LATE = re.compile(r"pacific|west|mountain|alternate|\+1|\+2", re.I)
# Nomes mais bonitos (com acentos).
PRETTY = {
    "RTP Memoria": "RTP Memória", "RTP Acores": "RTP Açores", "RTP Noticias": "RTP Notícias",
    "SIC Noticias": "SIC Notícias", "TVI Ficcao": "TVI Ficção", "SIC Alta Definicao": "SIC Alta Definição",
    "Euronews Portuguese": "Euronews Português", "FIFA+ Portuguese": "FIFA+ Português",
}


def fetch(url, headers=None, timeout=60, tries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def norm(name):
    """'RTP 1 (720p) [Not 24/7]' e 'RTP.1.HD.pt' -> 'rtp1'."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\[.*?\]|\(.*?\)", " ", s)
    s = re.sub(r"\.(pt|us2?|uk|fr)$", "", s)
    s = re.sub(r"\b(hd|sd|fhd|uhd|4k|east|portugal|usa|us|uk|france|by pluto tv|pluto tv)\b", " ", s)
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
    for _ in range(2):  # um timeout isolado não mata o canal
        try:
            with urllib.request.urlopen(urllib.request.Request(ch["url"], headers=headers), timeout=12) as r:
                r.read(512)
            return True
        except urllib.error.HTTPError as e:
            return e.code in (401, 403, 451, 429)
        except TimeoutError:
            continue
        except Exception:
            return False
    return False


def clean_name(name):
    name = re.sub(r"\s*\[.*?\]", "", name)
    name = re.sub(r"\s*\((\d+p|Portugal|United States|United Kingdom|France)\)", "", name)
    name = re.sub(r"\s+(HD|SD|HD East|East)$", "", name.strip())
    name = re.sub(r"^Pluto TV\s+|\s+(by\s+)?Pluto TV$", "", name)
    return PRETTY.get(name, name)


def resolution(name):
    m = re.search(r"\((\d+)p\)", name)
    return int(m.group(1)) if m else 0


def load_epg(files):
    """Devolve (raízes XML, mapa nome-normalizado -> id do canal)."""
    roots, ids = [], {}
    for f in files:
        root = ET.fromstring(fetch(f"https://epgshare01.online/epgshare01/epg_ripper_{f}.xml.gz"))
        roots.append(root)
        # Primeiro as versões "normais", depois as do Pacífico/+1: setdefault fica com a melhor.
        chans = sorted(root.findall("channel"), key=lambda c: bool(EPG_LATE.search(c.get("id"))))
        for c in chans:
            ids.setdefault(norm(c.get("id")), c.get("id"))
            for dn in c.findall("display-name"):
                ids.setdefault(norm(dn.text or ""), c.get("id"))
    return roots, ids


def country_channels(code, group, epg_ids, need_epg):
    items = parse_m3u(fetch(f"https://iptv-org.github.io/iptv/countries/{code}.m3u").decode())
    # Juntar as várias versões de cada canal (HD, SD, 720p...) pela base do tvg-id.
    variants = {}
    for ch in items:
        cats = ch["attrs"].get("group-title", "").split(";")
        if not KEEP_CATS.intersection(cats) or SKIP.search(ch["name"]):
            continue
        if need_epg and "Not 24/7" in ch["name"]:
            continue
        name = clean_name(ch["name"])
        key = ch["attrs"].get("tvg-id", "").split("@")[0].lower() or norm(name)
        ch.update(clean=name, cats=cats)
        variants.setdefault(key, []).append(ch)

    def pick(chs):
        # Melhor resolução primeiro; a primeira versão que responde fica.
        chs = sorted(chs, key=lambda c: (" SD" in c["name"], -resolution(c["name"])))
        epg = EPG_ALIAS.get(norm(chs[0]["clean"])) or next(
            (epg_ids[norm(c["clean"])] for c in chs if norm(c["clean"]) in epg_ids), "")
        if need_epg and not epg:
            return None
        for c in chs:
            if alive(c):
                theme = next((THEME_ORDER.index(x) for x in c["cats"] if x in THEME_ORDER), len(THEME_ORDER))
                return dict(c, group=group, epg=epg, order=theme)
        return None

    with cf.ThreadPoolExecutor(24) as ex:
        chosen = [c for c in ex.map(pick, variants.values()) if c]
    # Dois tvg-id diferentes com o mesmo nome visível: fica um.
    seen, out = set(), []
    for c in chosen:
        if norm(c["clean"]) not in seen:
            seen.add(norm(c["clean"]))
            out.append(c)
    return out


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


def pluto_region(ip, groups):
    now = NOW.replace(minute=0, second=0, microsecond=0)
    channels = {}
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
            prev = channels.get(ch["_id"])
            if prev:
                prev["timelines"] += ch.get("timelines", [])
            else:
                ch["group"] = groups[ch["category"]]
                name = re.sub(r"^Pluto TV\s+|\s+(by\s+)?Pluto TV$|\s+(BR|UK|FR)$", "", ch["name"].strip())
                ch["clean"] = re.sub(r"\s+", " ", name)
                channels[ch["_id"]] = ch
    return list(channels.values())


def xmltv_time(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y%m%d%H%M%S +0000")


def parse_xmltv_time(s):
    return dt.datetime.strptime(s.strip(), "%Y%m%d%H%M%S %z")


# ---------- Lista anterior (para fontes que falhem) ----------

def previous_entries():
    path = OUT / "tv.m3u"
    if not path.exists():
        return []
    out = []
    for ch in parse_m3u(path.read_text(encoding="utf-8")):
        a = ch["attrs"]
        if a.get("group-title") == "⭐ Favoritos":
            continue
        out.append(dict(group=a.get("group-title"), name=ch["name"], url=ch["url"], tvg=a.get("tvg-id", ""),
                        logo=a.get("tvg-logo", ""), opts=ch["opts"], order=0, src=a.get("x-src", "")))
    return out


# ---------- Saída ----------

def main():
    old = previous_entries()
    entries, epg_roots, failed = [], [], []

    for code, group, files, need_epg in COUNTRIES:
        try:
            roots, ids = load_epg(files)
            chans = country_channels(code, group, ids, need_epg)
            if not chans:
                raise RuntimeError("lista vazia")
        except Exception as e:
            print(f"AVISO: {code} falhou ({e}); uso a lista anterior", file=sys.stderr)
            failed.append(code)
            entries += [o for o in old if o["src"] == code]
            continue
        epg_roots += roots
        for c in chans:
            a = c["attrs"]
            opts = list(c["opts"])
            if a.get("http-user-agent"):
                opts.append(f'#EXTVLCOPT:http-user-agent={a["http-user-agent"]}')
            if a.get("http-referrer"):
                opts.append(f'#EXTVLCOPT:http-referrer={a["http-referrer"]}')
            entries.append(dict(group=group, name=c["clean"], url=c["url"], tvg=c["epg"],
                                logo=a.get("tvg-logo", ""), opts=opts, order=c["order"], src=code))

    country_names = {norm(e["name"]) for e in entries}
    pluto, seen_pluto = [], set()
    for region, ip, groups in PLUTO:
        src = f"pluto-{region}"
        try:
            chans = pluto_region(ip, groups)
            if not chans:
                raise RuntimeError("lista vazia")
        except Exception as e:
            print(f"AVISO: Pluto {region} falhou ({e}); uso a lista anterior", file=sys.stderr)
            failed.append(src)
            entries += [o for o in old if o["src"] == src]
            continue
        for c in chans:
            key = norm(c["clean"])
            # O mesmo canal noutra região ou já presente nos países: fica o primeiro.
            if c["_id"] in seen_pluto or key in country_names or (c["group"], key) in seen_pluto:
                continue
            seen_pluto.update({c["_id"], (c["group"], key)})
            pluto.append(c)
            logo = (c.get("colorLogoPNG") or c.get("logo") or {}).get("path", "")
            entries.append(dict(group=c["group"], name=c["clean"], url=f'https://jmp2.uk/plu-{c["_id"]}.m3u8',
                                tvg=f'pluto.{c["_id"]}', logo=logo, opts=[], order=0, src=src))

    # Proteção: uma lista muito mais pequena do que a anterior não é publicada.
    if old and len(entries) < 0.7 * len(old):
        sys.exit(f"ERRO: só {len(entries)} canais (antes {len(old)}). Lista anterior mantida.")

    entries.sort(key=lambda e: (GROUP_NAMES.index(e["group"]), e["order"], e["name"].lower()))

    # Números fixos por grupo.
    counter = {g: start for g, start in GROUPS}
    by_exact, by_norm = {}, {}
    for e in entries:
        e["chno"] = counter[e["group"]]
        counter[e["group"]] += 1
        by_exact.setdefault(e["name"].lower(), e)
        by_norm.setdefault(norm(e["name"]), e)
    favs = []
    for i, fav in enumerate(FAVORITOS, 1):
        # Nome exato primeiro: "CNN" não pode apanhar a "CNN Portugal".
        e = by_exact.get(fav.lower()) or by_norm.get(norm(fav))
        if e and any(f["url"] == e["url"] for f in favs):
            continue
        if e:
            favs.append(dict(e, group="⭐ Favoritos", chno=i))
    numbered = favs + entries

    lines = [f'#EXTM3U url-tvg="{REPO_RAW}/epg.xml.gz" x-tvg-url="{REPO_RAW}/epg.xml.gz"']
    for e in numbered:
        name = e["name"].replace(",", " ")
        lines.append(f'#EXTINF:-1 tvg-chno="{e["chno"]}" tvg-id="{e["tvg"]}" tvg-name="{name}" '
                     f'tvg-logo="{e["logo"]}" group-title="{e["group"]}" x-src="{e["src"]}",{name}')
        lines += list(dict.fromkeys(e["opts"]))
        lines.append(e["url"])
    (OUT / "tv.m3u").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Guia: só os canais usados, das 2 h passadas às 48 h seguintes, sem campos pesados.
    win_start, win_end = NOW - dt.timedelta(hours=2), NOW + dt.timedelta(hours=48)
    wanted = {e["tvg"] for e in entries if e["tvg"] and not e["tvg"].startswith("pluto.")}
    tv = ET.Element("tv", {"generator-info-name": "tv-casa"})
    added = set()
    for root in epg_roots:
        for c in root.findall("channel"):
            if c.get("id") in wanted and c.get("id") not in added:
                added.add(c.get("id"))
                tv.append(c)
    for c in pluto:
        el = ET.SubElement(tv, "channel", id=f'pluto.{c["_id"]}')
        ET.SubElement(el, "display-name").text = c["clean"]
    heavy = {"credits", "rating", "star-rating", "previously-shown", "video", "audio", "subtitles", "length"}
    for root in epg_roots:
        for p in root.findall("programme"):
            if p.get("channel") not in wanted:
                continue
            try:
                if parse_xmltv_time(p.get("stop")) < win_start or parse_xmltv_time(p.get("start")) > win_end:
                    continue
            except (ValueError, TypeError, AttributeError):
                pass
            for child in list(p):
                if child.tag in heavy:
                    p.remove(child)
            tv.append(p)
    for c in pluto:
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
    # mtime=0: o ficheiro só muda quando o guia muda.
    with gzip.GzipFile(OUT / "epg.xml.gz", "wb", mtime=0) as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="utf-8"))

    summary = [f"Atualizado: {NOW:%Y-%m-%d %H:%M} UTC",
               f"Canais: {len(entries)} (com guia: {sum(1 for e in entries if e['tvg'])}), favoritos: {len(favs)}"]
    summary += [f"  {g}: {sum(1 for e in numbered if e['group'] == g)}" for g in GROUP_NAMES]
    if failed:
        summary.append("Fontes com falha (lista anterior usada): " + ", ".join(failed))
    (OUT / "estado.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
