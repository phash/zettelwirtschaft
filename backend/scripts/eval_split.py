"""Auswertung der Aufteilungs-Erkennung an lokalen Stapel-PDFs (Spezifikation 12).

NICHT Teil der CI. Die Beispiel-Stapel bleiben ausserhalb des Repos.

Fuehrt dieselbe Seitenanalyse wie der Queue-Worker aus (Rendern, Tinte,
Leerseite, OCR 300 dpi bzw. digitaler Text, Randcode, Merkmale) und danach
das Scoring. Ausgegeben werden NUR Merkmale, Scores, Entscheidungen und
Gruende — nie Seitentext, Referenznummern oder IBANs (die stehen auch in den
Merkmalen nur als Hash).

Der KI-Pass wird NICHT ausgefuehrt (kein Ollama-Aufruf); ausgegeben wird,
ob er im Worker laufen wuerde (AUTO) und fuer wie viele Uebergaenge.

Aufruf (aus ``backend/``)::

    .venv/Scripts/python.exe scripts/eval_split.py <pdf-oder-ordner> [ground_truth.json] [--all]

``ground_truth.json``: ``{"stapel.pdf": [2, 17], "einzeln.pdf": []}`` — die
Seiten, NACH denen ein neues Dokument beginnt. Damit werden Precision/Recall
der Schnitte und der Anteil unnoetiger Pruefungen (AUTO: Stapel ohne echte
Grenze, der trotzdem in die Pruefung ginge) berechnet.

``--all`` zeigt jeden Uebergang, sonst nur Schnitte, unsichere und die
knappsten "kein Schnitt"-Stellen.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _use_repo_tools() -> None:
    """poppler/Tesseract aus ``tools/`` (Native-Build) nutzen, falls vorhanden."""
    tools = BACKEND.parent / "tools"
    poppler = tools / "poppler" / "Library" / "bin"
    tesseract = tools / "tesseract"
    paths = [str(p) for p in (poppler, tesseract) if p.is_dir()]
    if paths:
        os.environ["PATH"] = os.pathsep.join([*paths, os.environ.get("PATH", "")])
    if (tesseract / "tessdata").is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(tesseract / "tessdata"))


def detect_file(path: Path, settings, thumb_dir: Path) -> dict:
    from app.services import ocr_service, pdf_split_service
    from app.services.split_detection_service import analyze_page, build_proposal

    started = time.monotonic()
    geometry = pdf_split_service.pdf_page_geometry(path)
    digital = ocr_service.read_pdf_text_pages(path) or []
    deadline = started + settings.SPLIT_TIME_BUDGET_S
    pages, page_ocr, partial_from = [], [], None
    for n, geom in enumerate(geometry, start=1):
        if partial_from is None and time.monotonic() > deadline:
            partial_from = n
        features, entry = analyze_page(
            path, n, geom, digital[n - 1] if n - 1 < len(digital) else None, thumb_dir, settings,
            partial_from is None,
        )
        pages.append(features)
        page_ocr.append(entry)  # nur im Speicher, wird nie ausgegeben
    proposal = build_proposal(pages, partial_from)
    return {"pages": pages, "page_ocr": page_ocr, "proposal": proposal,
            "ms": int((time.monotonic() - started) * 1000)}


def llm_candidates(result: dict) -> list:
    """Uebergaenge, die der KI-Pass im Worker anfragen wuerde (AUTO)."""
    from app.services.split_llm_service import build_candidates, select_candidates

    texts = {e["page"]: e.get("text") or "" for e in result["page_ocr"]}
    selected = select_candidates(result["proposal"]["boundaries"], forced=False)
    return build_candidates(selected, result["pages"], texts)


def _page_summary(page: dict) -> str:
    sig = page.get("sig") or {}
    flags = []
    if page.get("blank"):
        flags.append("leer")
    if sig.get("counter"):
        x, y = sig["counter"]
        flags.append(f"Seite {x}" + (f"/{y}" if y else ""))
    if sig.get("addr"):
        flags.append("Anschrift")
    if sig.get("salut"):
        flags.append("Anrede")
    if sig.get("refs"):
        flags.append(f"{len(sig['refs'])} Ref.")
    if sig.get("ibans"):
        flags.append(f"{len(sig['ibans'])} IBAN/USt")
    if sig.get("margin"):
        flags.append("Rand {}/{}".format(*sig["margin"]))
    return (
        f"  S.{page['n']:>3}  {page['w']:.1f}x{page['h']:.1f}pt  Tinte {page.get('ink', 0):.4f}  "
        f"{page.get('source', '?'):<7} Konf. {page.get('conf', 0):.2f}  {', '.join(flags)}"
    )


def report(name: str, result: dict, truth: list[int] | None, show_all: bool, show_pages: bool) -> dict:
    proposal = result["proposal"]
    boundaries = proposal["boundaries"]
    cuts = [b["after_page"] for b in boundaries if b["decision"] == "cut"]
    uncertain = [b["after_page"] for b in boundaries if b["decision"] == "uncertain"]
    print(f"\n=== {name}: {len(result['pages'])} Seiten, Erkennung {result['ms']} ms ===")
    print(f"Duplex: {proposal['duplex']} (conf {proposal['duplex_conf']})  "
          f"partial_from: {proposal['partial_from']}  Leerseiten: {proposal['blank_kinds'] or '-'}")
    candidates = llm_candidates(result)
    inside = [c.after_page for c in candidates if c.inside_counter]
    print(f"Schnitte nach Seite: {cuts or '-'}   unsicher: {uncertain or '-'}   "
          f"KI-Aufrufe (AUTO): {1 if candidates else 0} "
          f"({len(candidates)} Übergänge, davon im Zähler: {inside or '-'})")
    if show_pages:
        for page in result["pages"]:
            print(_page_summary(page))
    nearest = sorted(
        (b for b in boundaries if b["decision"] == "none" and b["score"] is not None),
        key=lambda b: -b["score"],
    )[:3]
    shown = boundaries if show_all else [b for b in boundaries if b["decision"] != "none"] + nearest
    for b in sorted(shown, key=lambda b: b["after_page"]):
        score = "  n/a" if b["score"] is None else f"{b['score']:+5.1f}"
        p = "  n/a " if b["p"] is None else f"{b['p']:.4f}"
        print(f"  {b['after_page']:>3}|{b['after_page'] + 1:<3} {score}  p={p}  {b['decision']:<9} "
              + "; ".join(b["reasons"]))
    stats = {"tp": 0, "fp": 0, "fn": 0, "review": bool(cuts or uncertain), "truth_has_cut": None}
    if truth is not None:
        expected = set(truth)
        stats.update(
            tp=len(expected & set(cuts)), fp=len(set(cuts) - expected), fn=len(expected - set(cuts)),
            truth_has_cut=bool(expected),
        )
        print(f"Erwartet: {sorted(expected) or '-'}  ->  TP {stats['tp']}  FP {stats['fp']}  FN {stats['fn']}")
    return stats


def main() -> int:
    # Gruende enthalten "→"/Umlaute: umgeleitete Ausgabe unter Windows waere
    # sonst cp1252 und braeche mit UnicodeEncodeError ab.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="PDF-Datei oder Ordner mit PDFs")
    parser.add_argument("ground_truth", type=Path, nargs="?", help="JSON {datei: [Schnitt nach Seite, ...]}")
    parser.add_argument("--all", action="store_true", help="alle Uebergaenge ausgeben")
    parser.add_argument("--pages", action="store_true", help="Merkmale pro Seite ausgeben (ohne Text)")
    args = parser.parse_args()

    _use_repo_tools()
    from app.config import Settings

    settings = Settings()
    files = sorted(args.source.glob("*.pdf")) if args.source.is_dir() else [args.source]
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8")) if args.ground_truth else {}

    totals = {"tp": 0, "fp": 0, "fn": 0}
    no_cut_files = needless_reviews = 0
    with tempfile.TemporaryDirectory(prefix="eval_split_") as tmp:
        for path in files:
            result = detect_file(path, settings, Path(tmp) / path.stem)
            stats = report(path.name, result, truth.get(path.name), args.all, args.pages)
            for key in totals:
                totals[key] += stats[key]
            if stats["truth_has_cut"] is False:
                no_cut_files += 1
                needless_reviews += int(stats["review"])

    if truth:
        precision = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else 1.0
        recall = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else 1.0
        print(f"\nPrecision {precision:.2f}  Recall {recall:.2f}  "
              f"unnötige Prüfungen {needless_reviews}/{no_cut_files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
