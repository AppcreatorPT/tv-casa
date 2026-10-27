# tv-casa

Lista IPTV arrumada, com guia de TV. Atualiza-se sozinha de 6 em 6 horas (GitHub Actions).
O estado da última atualização está em [`estado.txt`](estado.txt).

## Endereços para o TiviMate

- Lista: `https://raw.githubusercontent.com/AppcreatorPT/tv-casa/main/tv.m3u`
- Guia: `https://raw.githubusercontent.com/AppcreatorPT/tv-casa/main/epg.xml.gz` (a lista já o indica)

## Grupos e números

| Grupo | Números |
|---|---|
| ⭐ Favoritos (RTP 1 = 1, RTP 2 = 2, SIC = 3, TVI = 4…) | 1–99 |
| 🇵🇹 Portugal | 100 |
| 🇺🇸 EUA | 200 |
| 🇬🇧 Reino Unido | 400 |
| 🇫🇷 França | 500 |
| 🎬 Filmes · Brasil / 📺 Séries · Brasil | 600 / 700 |
| 🎬 Filmes · Inglês / 📺 Séries · Inglês | 800 / 900 |
| 🎬 Filmes · Francês / 📺 Séries · Francês | 1200 / 1300 |
| 🍥 Anime · 🧸 Infantil · 🌍 Documentários | 1400 · 1500 · 1600 |
| ⚽ Desporto · 📰 Notícias · 🎵 Música | 1700 · 1800 · 1900 |

Os favoritos têm número fixo. Nos outros grupos, um canal que morre só mexe nos números do seu grupo.

## Fontes

- [iptv-org](https://github.com/iptv-org/iptv): canais públicos de Portugal, EUA, Reino Unido e França.
  Dos países estrangeiros só entram canais com guia de TV.
- Pluto TV (Brasil, EUA, Reino Unido, França): canais temáticos grátis. Os do Brasil são dobrados em pt-BR.
  Não há Pluto, Samsung TV Plus ou Plex para Portugal.
- Canais oficiais extra de Portugal (RTP África, RTP Mundo, TVI Internacional): lista `EXTRA` em `gerar.py`.
- [epgshare01](https://epgshare01.online): guia dos canais dos países.

## Como funciona

- Os canais mortos saem na atualização seguinte. De cada canal fica a melhor versão que funciona (HD primeiro).
- Se uma fonte falhar, os canais dela vêm da lista anterior.
- Se a lista nova tiver menos de 70% dos canais da anterior, não é publicada e a Action falha (o GitHub avisa por e-mail).
- Para mudar grupos, favoritos ou filtros, edite `gerar.py`.
