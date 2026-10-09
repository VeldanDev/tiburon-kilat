"""Berita Kilat Tiburon, versi GitHub Actions: berita teknologi penting dikirim ke Telegram
begitu terbit, tanpa laptop menyala. Workflow memanggil skrip ini tiap 5 menit.

Penyaring tanpa model: judul menyebut nama besar DAN peristiwa besar, atau cerita
Hacker News dengan skor >= SKOR_HN. Judul diterjemahkan dengan Gemini kalau kuncinya
ada; kalau gagal, judul asli yang dikirim. Daftar berita yang sudah diproses disimpan di
state/kilat_sudah.json (dibawa antar-jalan lewat actions/cache).
"""
import datetime
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

import sumber

SUDAH = Path(__file__).parent / "state" / "kilat_sudah.json"
MAKS_KIRIM = 4
SKOR_HN = 300
SEGAR_JAM = 6
WIB = datetime.timezone(datetime.timedelta(hours=7))
JAM_SENYAP = range(0, 6)  # 00:00-05:59 WIB tidak mengirim; tercakup Radar Pagi

ENTITAS = re.compile(
    r"\b(openai|anthropic|claude|chatgpt|gemini|deepmind|google|alphabet|apple|microsoft|meta|nvidia|tesla"
    r"|spacex|amazon|aws|xai|grok|mistral|deepseek|tiktok|bytedance|samsung|intel|amd|github|linux|android"
    r"|iphone|windows|youtube|whatsapp|instagram|cursor|hugging ?face|perplexity|tsmc|qualcomm|arm"
    r"|playstation|ps5|ps6|xbox|nintendo|switch 2|steam|valve|geforce|rtx|gta|rockstar|epic games|ubisoft"
    r"|unreal|capcom|sony)\b", re.I)
PERISTIWA = re.compile(
    r"\b(launch\w*|releas\w*|unveil\w*|announc\w*|introduc\w*|debut\w*|acquir\w*|acquisition|buys?|bought"
    r"|raises?|raised|funding|valuation|ipo|layoffs?|lays off|cuts? \d|breach\w*|hack\w*|leak\w*|outage"
    r"|down for|bans?|banned|lawsuit|sues?|sued|fined?|antitrust|recall\w*|shut\w* down|open[- ]sourc\w*"
    r"|new model|gpt-?\d|agent|rolls? out|trailer|release date|delay\w*|revealed?)\b", re.I)


def penting(i: dict) -> bool:
    if i.get("resmi"):  # pengumuman langsung dari lab AI selalu dikirim
        return True
    if i.get("sumber") == "Hacker News":
        return (i.get("skor") or 0) >= SKOR_HN
    j = i.get("judul") or ""
    return bool(ENTITAS.search(j) and PERISTIWA.search(j))


def muat() -> dict:
    return json.loads(SUDAH.read_text(encoding="utf-8")) if SUDAH.exists() else {}


def simpan(d: dict) -> None:
    SUDAH.parent.mkdir(exist_ok=True)
    terbaru = dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:3000])
    SUDAH.write_text(json.dumps(terbaru, ensure_ascii=False), encoding="utf-8")


def _post(url: str, data: dict, json_body: bool = False) -> dict:
    if json_body:
        body, kepala = json.dumps(data).encode(), {"Content-Type": "application/json"}
    else:
        body, kepala = urllib.parse.urlencode(data).encode(), {}
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=kepala), timeout=40) as r:
        return json.loads(r.read().decode())


def terjemah(judul: list[str]) -> list[str]:
    kunci = os.environ.get("GEMINI_API_KEY")
    if not kunci:
        return judul
    perintah = ("Terjemahkan tiap judul berita teknologi ke bahasa Indonesia yang wajar dan singkat. "
                "Nama produk/perusahaan tetap. Balas hanya daftar bernomor dengan urutan sama, satu judul "
                "per baris, tanpa tambahan apa pun.\n\n" + "\n".join(f"{n + 1}. {j}" for n, j in enumerate(judul)))
    try:
        r = _post("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key="
                  + kunci, {"contents": [{"parts": [{"text": perintah}]}]}, json_body=True)
        teks = r["candidates"][0]["content"]["parts"][0]["text"]
        baris = [re.sub(r"^\s*\d+[.)]\s*", "", b).strip() for b in teks.strip().splitlines() if b.strip()]
        if len(baris) == len(judul) and all(baris):
            return baris
    except Exception as e:
        print(f"terjemah gagal, pakai judul asli: {str(e)[:120]}")
    return judul


def kirim(items: list[dict]) -> None:
    judul = terjemah([i["judul"] for i in items])
    baris = ["Berita Kilat Tiburon", ""]
    for i, j in zip(items, judul):
        label = ("RESMI " if i.get("resmi") else "") + i["sumber"] + (f", {i['skor']} poin" if i.get("skor") else "")
        baris += [j, f"({label}) {i['url']}", ""]
    r = _post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
              {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": "\n".join(baris).strip(),
               "disable_web_page_preview": "true"})
    if not r.get("ok"):
        raise RuntimeError(f"Telegram menolak: {r}")


def main() -> None:
    sudah = muat()
    awal = not sudah
    sekarang = datetime.datetime.now(WIB)
    cap = sekarang.isoformat(timespec="minutes")

    items = []
    try:
        items += sumber.ambil_berita_dunia(per_feed=15, max_umur_jam=SEGAR_JAM)
    except Exception as e:
        print(f"berita dunia gagal: {e}")
    try:
        items += sumber.ambil_resmi(SEGAR_JAM, set(sudah))
    except Exception as e:
        print(f"kanal resmi gagal: {e}")
    try:
        items += sumber.ambil_telegram(SEGAR_JAM)
    except Exception as e:
        print(f"telegram gagal: {e}")
    try:
        items += sumber.ambil_hacker_news(30)
    except Exception as e:
        print(f"hacker news gagal: {e}")

    for nama in {i["sumber"] for i in items if i.get("halaman")}:
        kunci = f"__halaman__{nama}"
        if kunci not in sudah:
            for i in items:
                if i.get("sumber") == nama:
                    sudah[i["url"]] = "awal"
            sudah[kunci] = cap
    calon = []
    for i in items:
        url = i.get("url")
        if not url or url in sudah:
            continue
        if i.get("sumber") == "Hacker News":
            # Ditandai saat terkirim saja, supaya cerita yang belakangan menembus skor tetap terkirim.
            if penting(i):
                calon.append(i)
        else:
            sudah[url] = cap
            if penting(i):
                calon.append(i)

    if awal:
        for i in calon:
            sudah[i["url"]] = "awal"
        simpan(sudah)
        print(f"jalan pertama: {len(items)} berita ditandai, tidak dikirim")
        return

    if calon and sekarang.hour not in JAM_SENYAP:
        # Kanal resmi lab AI dulu, baru sisanya menurut skor.
        calon.sort(key=lambda i: (not i.get("resmi"), -(i.get("skor") or 0)))
        kiriman = calon[:MAKS_KIRIM]
        kirim(kiriman)
        for i in kiriman:
            sudah[i["url"]] = cap
        print(f"terkirim {len(kiriman)}: " + " | ".join(i["judul"][:50] for i in kiriman))
    else:
        print(f"{len(items)} berita, {len(calon)} calon, tidak ada yang dikirim")
    simpan(sudah)


if __name__ == "__main__":
    main()
