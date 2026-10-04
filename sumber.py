#!/usr/bin/env python3
"""Radar Pagi Tiburon — sumber.py

Langkah 1-2 dari PRD-RADAR.md: ambil item dari Hacker News dan GitHub
Trending, cetak ke layar. Belum ada penyaringan (saring.py) atau
pengiriman (kirim.py) -- itu langkah berikutnya, sengaja belum dikerjakan.

Prinsip dari claude-managed-agents-arsitektur.md yang dipakai di sini:
pengambilan data (sumber.py) dipisah dari penalaran (saring.py, belum
ada). Modul ini cuma bertugas ambil dan cetak, tidak menilai relevansi.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# Konsol Windows sering default ke codepage non-UTF-8 (cp1252/cp437), yang
# merusak karakter seperti bintang (★) dan em-dash (—) dari judul/deskripsi
# sumber. Sama seperti tiburon.py.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

USER_AGENT = "tiburon-radar-pagi/0.1 (skrip pribadi, bukan bot publik)"
TIMEOUT = 10  # detik per request -- gagal cepat, bukan menggantung


def _ambil_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def ambil_hacker_news(jumlah: int = 10) -> list[dict]:
    """Ambil top stories dari Hacker News API resmi.

    hacker-news.firebaseio.com tidak butuh API key. topstories.json
    mengembalikan daftar ID cerita terurut skor; tiap ID di-fetch
    terpisah lewat item/{id}.json -- itu caranya API ini didesain,
    bukan pilihan kita.
    """
    id_list = _ambil_json("https://hacker-news.firebaseio.com/v0/topstories.json")
    hasil = []
    for item_id in id_list[:jumlah]:
        try:
            item = _ambil_json(
                f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[peringatan] gagal ambil item HN {item_id}: {exc}", file=sys.stderr)
            continue
        if not item or item.get("type") != "story":
            continue
        hasil.append(
            {
                "sumber": "Hacker News",
                "judul": item.get("title", "(tanpa judul)"),
                "url": item.get("url") or f"https://news.ycombinator.com/item?id={item_id}",
                "skor": item.get("score", 0),
                "komentar": item.get("descendants", 0),
                "diskusi": f"https://news.ycombinator.com/item?id={item_id}",
            }
        )
    return hasil


def ambil_github_trending(jumlah: int = 10, hari: int = 7) -> list[dict]:
    """Approksimasi GitHub Trending lewat Search API resmi.

    github.com/trending tidak punya API resmi dan HTML-nya bisa berubah
    kapan saja tanpa pemberitahuan -- scraping itu rapuh untuk skrip yang
    mau jalan tiap pagi tanpa diawasi. Search API
    (api.github.com/search/repositories) resmi, terdokumentasi, dan
    hasilnya JSON langsung lewat urllib tanpa dependency tambahan.

    Trade-off yang disadari: ini BUKAN replika algoritma trending GitHub
    (yang mempertimbangkan kecepatan penambahan star per hari), tapi
    proxy yang cukup dekat -- repo yang dibuat dalam `hari` terakhir,
    diurutkan berdasar jumlah star.
    """
    sejak = (datetime.now(timezone.utc) - timedelta(days=hari)).strftime("%Y-%m-%d")
    query = urllib.parse.quote(f"created:>{sejak}")
    url = (
        "https://api.github.com/search/repositories"
        f"?q={query}&sort=stars&order=desc&per_page={jumlah}"
    )
    data = _ambil_json(url)
    hasil = []
    for repo in data.get("items", [])[:jumlah]:
        hasil.append(
            {
                "sumber": "GitHub Trending",
                "judul": repo.get("full_name", "(tanpa nama)"),
                "url": repo.get("html_url", ""),
                "bintang": repo.get("stargazers_count", 0),
                "bahasa": repo.get("language") or "-",
                "deskripsi": repo.get("description") or "(tanpa deskripsi)",
            }
        )
    return hasil


# ---------------------------------------------------------------------------
# Berita teknologi dunia (RSS)
# ---------------------------------------------------------------------------
#
# Bagian ini melayani kebutuhan yang berbeda dari dua fungsi di atas. Hacker
# News dan GitHub Trending menjawab "apa yang sedang dibicarakan engineer";
# feed di bawah menjawab "apa yang terjadi di industri teknologi dunia" --
# pendanaan startup, rilis perusahaan besar, kebijakan, pasar.
#
# Semuanya RSS/Atom publik tanpa API key, dan tiap feed gagal sendiri-sendiri.
# Dua sumber sengaja TIDAK dipakai walau sempat dicoba (2026-09-03):
#   - VentureBeat dengan garis miring di akhir (`/feed/`) membalas HTTP 308;
#     yang dipakai versi tanpa garis miring.
#   - Tech in Asia membalas HTTP 403 -- memblokir klien non-browser. Jangan
#     dicoba lagi dengan menyamar sebagai browser; itu melawan maunya mereka.
#
# Campuran wilayahnya disengaja: Sifted untuk Eropa, Rest of World untuk pasar
# di luar AS/Eropa. Tanpa itu, "berita dunia" pada praktiknya cuma jadi berita
# Silicon Valley.
FEED_BERITA = {
    "TechCrunch": "https://techcrunch.com/feed/",
    "TechCrunch Startups": "https://techcrunch.com/category/startups/feed/",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/index",
    "VentureBeat": "https://venturebeat.com/feed",
    "Engadget": "https://www.engadget.com/rss.xml",
    "Wired": "https://www.wired.com/feed/rss",
    "Rest of World": "https://restofworld.org/feed/latest",
    "Sifted": "https://sifted.eu/feed",
    "MIT Technology Review": "https://www.technologyreview.com/feed/",
}

# Judul RSS sering membawa sisa markup dan entitas HTML (&amp;#8217;).
_TAG = re.compile(r"<[^>]+>")


def _bersih(teks: str | None) -> str:
    """Buang tag HTML dan urai entitas, dua kali -- feed sering meng-escape ganda."""
    if not teks:
        return ""
    return " ".join(_TAG.sub(" ", html.unescape(html.unescape(teks))).split())


def _waktu_terbit(teks: str | None) -> datetime | None:
    """Urai tanggal RSS (RFC-822) maupun Atom (ISO-8601); None kalau tak terbaca.

    Item tanpa tanggal yang terbaca TIDAK dibuang (lihat ambil_berita_dunia) --
    lebih baik memuat satu item tua daripada membuang feed yang formatnya
    sedikit menyimpang.
    """
    if not teks:
        return None
    teks = teks.strip()
    try:
        return parsedate_to_datetime(teks)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(teks.replace("Z", "+00:00"))
    except ValueError:
        return None


def _item_dari_feed(xml_teks: str, nama: str) -> list[dict]:
    """Ambil judul/tautan/waktu dari satu dokumen RSS atau Atom.

    Dipakai ElementTree, bukan regex: feed-feed ini XML sungguhan, dan parser
    XML tidak akan salah menangkap tag di dalam CDATA seperti regex.
    """
    akar = ET.fromstring(xml_teks)
    hasil: list[dict] = []

    # RSS 2.0: channel/item. Atom: feed/entry dengan namespace.
    entri = akar.iter("item")
    hasil_rss = list(entri)
    if hasil_rss:
        for node in hasil_rss:
            judul = _bersih(node.findtext("title"))
            tautan = (node.findtext("link") or "").strip()
            if judul and tautan:
                hasil.append(
                    {
                        "sumber": nama,
                        "judul": judul,
                        "url": tautan,
                        "terbit": _waktu_terbit(node.findtext("pubDate")),
                    }
                )
        return hasil

    ns = "{http://www.w3.org/2005/Atom}"
    for node in akar.iter(f"{ns}entry"):
        judul = _bersih(node.findtext(f"{ns}title"))
        tautan = ""
        for tautan_node in node.iter(f"{ns}link"):
            if tautan_node.get("rel", "alternate") == "alternate":
                tautan = (tautan_node.get("href") or "").strip()
                break
        waktu = node.findtext(f"{ns}published") or node.findtext(f"{ns}updated")
        if judul and tautan:
            hasil.append(
                {"sumber": nama, "judul": judul, "url": tautan, "terbit": _waktu_terbit(waktu)}
            )
    return hasil


def ambil_berita_dunia(per_feed: int = 25, max_umur_jam: int = 36) -> list[dict]:
    """Kumpulkan berita teknologi dunia dari semua feed di FEED_BERITA.

    `max_umur_jam` 36 jam, bukan 24: radar jalan tiap pagi, dan berita yang
    terbit sore kemarin belum tentu sempat terbaca. Item yang tanggalnya tidak
    terbaca tetap dimuat -- feed yang formatnya menyimpang tidak boleh hilang
    diam-diam hanya karena tanggalnya aneh.

    Deduplikasi berdasar judul yang dinormalkan, karena TechCrunch muncul di
    dua feed (utama dan kategori Startups) dan artikel yang sama sering ada di
    keduanya.
    """
    batas = datetime.now(timezone.utc) - timedelta(hours=max_umur_jam)
    semua: list[dict] = []
    terlihat: set[str] = set()

    for nama, url in FEED_BERITA.items():
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                xml_teks = response.read().decode("utf-8", errors="replace")
            item_feed = _item_dari_feed(xml_teks, nama)
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            print(f"[peringatan] feed {nama} gagal: {exc}", file=sys.stderr)
            continue

        dimuat = 0
        for item in item_feed:
            if dimuat >= per_feed:
                break
            waktu = item.pop("terbit", None)
            if waktu is not None:
                if waktu.tzinfo is None:
                    waktu = waktu.replace(tzinfo=timezone.utc)
                if waktu < batas:
                    continue
            kunci = re.sub(r"[^a-z0-9]", "", item["judul"].lower())[:80]
            if kunci in terlihat:
                continue
            terlihat.add(kunci)
            semua.append(item)
            dimuat += 1
        print(f"[info] feed {nama}: {dimuat} item", file=sys.stderr)

    return semua


def cetak_berita_dunia(items: list[dict]) -> None:
    print(f"\n=== Berita teknologi dunia ({len(items)} item mentah) ===")
    if not items:
        print("(kosong -- semua feed gagal, lihat stderr)")
        return
    for item in items:
        print(f"- [{item['sumber']}] {item['judul']}")
        print(f"  {item['url']}")


def cetak_hacker_news(items: list[dict]) -> None:
    print(f"\n=== Hacker News ({len(items)} item) ===")
    if not items:
        print("(kosong -- sumber ini gagal, lihat stderr)")
        return
    for item in items:
        print(f"- {item['judul']}")
        print(f"  {item['url']}")
        print(f"  skor {item['skor']} | {item['komentar']} komentar | {item['diskusi']}")


def cetak_github_trending(items: list[dict]) -> None:
    print(f"\n=== GitHub Trending, 7 hari terakhir ({len(items)} item) ===")
    if not items:
        print("(kosong -- sumber ini gagal, lihat stderr)")
        return
    for item in items:
        print(f"- {item['judul']} [{item['bahasa']}] ★{item['bintang']}")
        print(f"  {item['url']}")
        print(f"  {item['deskripsi']}")


def main() -> None:
    # Tiap sumber gagal sendiri-sendiri -- satu sumber mati tidak boleh
    # menghentikan yang lain (prinsip "gagal dengan tenang" dari PRD).
    try:
        hn = ambil_hacker_news()
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[gagal] Hacker News: {exc}", file=sys.stderr)
        hn = []
    cetak_hacker_news(hn)

    try:
        gh = ambil_github_trending()
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[gagal] GitHub Trending: {exc}", file=sys.stderr)
        gh = []
    cetak_github_trending(gh)


if __name__ == "__main__":
    main()
