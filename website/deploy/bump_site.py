"""Hebt die Live-Seiten der Website von OLD auf NEW.

Aufruf: python bump_site.py OLD NEW LIVE_DIR OUT_DIR SUMS_FILE NOTES [DATUM]
LIVE_DIR: aktuelle Seiten vom Server (download.html, index.html, hilfe.html, latest.json).
OUT_DIR: dorthin schreibt das Skript die angepassten Seiten + SHA256SUMS.txt.
SUMS_FILE: sha256sum-Ausgabe aller NEW-Downloads (Name -> Hash); die Groessen
liest das Skript aus SIZES (Name -> Bytes, als JSON neben SUMS_FILE: sizes.json).
DATUM: published_at in latest.json (YYYY-MM-DD), sonst bleibt der alte Wert.
"""
import json
import re
import sys
from pathlib import Path

old, new, live, out, sums_file, notes = sys.argv[1:7]
LIVE, OUT = Path(live), Path(out)
OUT.mkdir(parents=True, exist_ok=True)
SUMS = {}
for line in Path(sums_file).read_text(encoding="utf-8").splitlines():
    h, name = line.split(maxsplit=1)
    SUMS[name.lstrip("*").strip()] = h
SIZES = json.loads((Path(sums_file).parent / "sizes.json").read_text(encoding="utf-8"))
ORDER = [f"zettelwirtschaft-{new}-source.tar.gz", f"zettelwirtschaft-{new}-source.zip",
         f"zettelwirtschaft-backend-{new}-docker-image.tar.gz", f"zettelwirtschaft-frontend-{new}-docker-image.tar.gz",
         f"Zettelwirtschaft-{new}-Native-Setup.exe", f"Zettelwirtschaft-{new}-native-update.zip"]
assert set(ORDER) == set(SUMS), (sorted(SUMS), ORDER)


def human(n: int) -> str:
    if n < 1024 * 1024:
        return f"{round(n / 1024)}&nbsp;KB"
    mb = n / 1024 / 1024
    return f"{mb:.1f}".replace(".", ",") + "&nbsp;MB" if mb < 10 else f"{round(mb)}&nbsp;MB"


def read(name):
    return (LIVE / name).read_bytes().decode("utf-8")


def write(name, text):
    (OUT / name).write_bytes(text.encode("utf-8"))


t = read("download.html")
# Groessen: die meta-Zeile jeder Download-Karte steht vor ihrem Link.
for name in ORDER:
    old_name = name.replace(new, old)
    pat = re.compile(r'(<div class="meta"><span>[^<]+</span><span>)([^<]+)(</span>(?:(?!<div class="meta">).)*?href="downloads/' + re.escape(old_name) + '")', re.DOTALL)
    size = human(SIZES[name])
    t, n = pat.subn(lambda m, size=size: m.group(1) + size + m.group(3), t, count=1)
    assert n == 1, f"Groesse fuer {old_name} nicht gefunden"
block = "\n".join(f"<b>{SUMS[n]}</b>  {n}" for n in ORDER)
t, n = re.subn(r'(<pre class="code reveal" data-d="1" style="margin-top:1.6rem">)<b>[0-9a-f]{64}</b>.*?(</pre>)',
               lambda m: m.group(1) + block + m.group(2), t, count=1, flags=re.DOTALL)
assert n == 1, "Pruefsummen-Block nicht gefunden"
write("download.html", t.replace(old, new))
for page in ("index.html", "hilfe.html"):
    write(page, read(page).replace(old, new))

data = json.loads(read("latest.json"))
data.update(latest_version=new, published_at=sys.argv[7] if len(sys.argv) > 7 else data["published_at"], notes=notes)
data["downloads"] = {k: v.replace(old, new) for k, v in data["downloads"].items()}
write("latest.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
write("SHA256SUMS.txt", "".join(f"{SUMS[n]}  {n}\n" for n in ORDER))
for f in sorted(OUT.iterdir()):
    txt = f.read_text(encoding="utf-8")
    print(f"{f.name}: {old}={txt.count(old)} {new}={txt.count(new)}")
