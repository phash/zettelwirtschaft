"""Reine Erkennung (Spezifikation 12, Etappe 2): Merkmale aus Seitentext,
Boilerplate, Duplex, Scoring und Schwellen.

Alle Fixtures sind synthetisch (erfundene Texte, Nummern und Hashes nach dem
Muster des 44-seitigen Beispiel-Stapels) — keine echten Scans, keine
Personendaten im Repo.
"""

import pytest

from app.services import split_weights as W
from app.services.split_detection_service import build_proposal, proposal_counts
from app.services.split_heuristics import (
    boilerplate_lines,
    detect_address_block,
    detect_duplex,
    extract_features,
    parse_counter,
    parse_margin_code,
    parse_refs,
    score_boundaries,
    short_hash,
    sigmoid,
    text_lines,
)

# ---------------------------------------------------------------------------
# Synthetische Seiten
# ---------------------------------------------------------------------------

NACHTRAG = short_hash("nachtrag zur versicherung")  # wiederholter Briefkopf
LOGO = short_hash("beispiel versicherung ag")
REF_A = short_hash("REFA0001")
REF_B = short_hash("REFB0002")


def page(n, *, h=840.0, w=591.12, blank=False, counter=None, refs=(), ibans=(), addr=False,
         salut=False, dates=(), header=(), margin=None, rot=0, source="ocr"):
    sig = None if source == "skipped" else {
        "counter": list(counter) if counter else None,
        "refs": list(refs), "ibans": list(ibans), "addr": addr, "salut": salut,
        "dates": list(dates), "header": list(header), "margin": list(margin) if margin else None,
    }
    return {"n": n, "w": w, "h": h, "rot": rot, "blank": blank, "source": "none" if blank else source,
            "sig": sig}


def sample_stack() -> list[dict]:
    """Muster des Beispiels: Anschreiben (2 Seiten, "Seite 1 von 2" / "2 von 2",
    Anschrift + Anrede) + Vertragsnachtrag (42 Seiten, "Seite 1..", gleiche
    Referenz, wiederholter Kopf "Nachtrag", Anschrift ohne Anrede), zwei leere
    Rueckseiten (12, 42), ueber die der Zaehler weiterlaeuft. Duplex: je Blatt
    gleiche Hoehe, zwischen Blaettern schwankend (Einzug misst jedes Blatt)."""
    heights = [836.64, 843.12, 841.68, 835.92, 844.56, 840.24]
    pages = [
        page(1, counter=(1, 2), ibans=("i1",), addr=True, salut=True, dates=("2026-03-01",),
             header=(LOGO, "l1", "l2"), margin=(1, 44)),
        page(2, counter=(2, 2), dates=("2026-03-01",), header=(LOGO, "l3"), margin=(2, 44)),
    ]
    x = 0
    for n in range(3, 45):
        x += 1
        if n in (12, 42):
            pages.append(page(n, blank=True))
            continue
        pages.append(page(
            n, counter=(x, None), refs=(REF_A,), addr=(n in (3, 7)), dates=("2026-02-20",),
            header=(NACHTRAG, LOGO, f"h{n}a", f"h{(n // 2) * 2}b"), margin=(n, 44),
            ibans=("i1",) if n == 3 else (),
        ))
    for p in pages:
        p["h"] = heights[((p["n"] - 1) // 2) % len(heights)]
    return pages


def by_page(boundaries):
    return {b["after_page"]: b for b in boundaries}


# ---------------------------------------------------------------------------
# Beispiel-Stapel
# ---------------------------------------------------------------------------


class TestSampleStack:
    def test_exactly_one_cut_after_page_2(self):
        proposal = build_proposal(sample_stack(), None)
        cuts = [b["after_page"] for b in proposal["boundaries"] if b["decision"] == "cut"]
        assert cuts == [2]
        assert proposal_counts(proposal) == (1, 0)  # kein unsicherer Uebergang -> 0 KI-Aufrufe
        assert proposal["duplex"] is True and proposal["duplex_conf"] >= 0.8

    def test_boundary_scores_match_expectation(self):
        proposal = build_proposal(sample_stack(), None)
        b = by_page(proposal["boundaries"])
        assert b[2]["p"] > 0.99
        assert "Seitenzähler beginnt neu: Seite 1" in b[2]["reasons"]
        assert b[1]["score"] <= -6
        assert all(b[i]["score"] <= -6 for i in range(3, 44)), [b[i]["score"] for i in range(3, 44)]

    def test_blank_backs_are_never_cut(self):
        proposal = build_proposal(sample_stack(), None)
        b = by_page(proposal["boundaries"])
        for i in (11, 12, 41, 42):
            assert b[i]["decision"] == "none"
            assert b[i]["score"] <= -8
        assert "Leere Rückseite – der Seitenzähler läuft über sie weiter" in b[11]["reasons"]
        assert proposal["blank_kinds"] == {"12": "back", "42": "back"}

    def test_counter_continues_over_blank_page(self):
        b = by_page(build_proposal(sample_stack(), None)["boundaries"])
        assert "Seitenzähler läuft weiter (Seite 9 → 11)" in b[11]["reasons"]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    def test_separator_sheet_with_restart_is_cut_once(self):
        pages = [
            page(1, counter=(1, 2)), page(2, counter=(2, 2)),
            page(3, blank=True),
            page(4, counter=(1, 1), addr=True, salut=True),
        ]
        boundaries, kinds = score_boundaries(pages)
        b = by_page(boundaries)
        assert [x["after_page"] for x in boundaries if x["decision"] == "cut"] == [2]
        assert "Leerseite als Trennblatt?" in b[2]["reasons"]
        assert b[3]["decision"] == "none"  # kein Teil nur aus der Leerseite
        assert kinds == {3: "separator"}

    def test_duplex_prefers_cut_after_blank_back(self):
        # Simplex-Brief im Duplex-Scan: Seite 2 ist die leere Rueckseite
        heights = [836.0, 836.0, 843.0, 843.0, 839.0, 839.0]
        pages = [
            page(1, h=heights[0], addr=True, salut=True),
            page(2, h=heights[1], blank=True),
            page(3, h=heights[2], counter=(1, 2), addr=True, salut=True),
            page(4, h=heights[3], counter=(2, 2)),
            page(5, h=heights[4], counter=(1, 1), addr=True, salut=True),
            page(6, h=heights[5], blank=True),
        ]
        duplex, conf = detect_duplex(pages)
        assert duplex and conf == 1.0
        boundaries, _ = score_boundaries(pages, duplex_conf=conf)
        b = by_page(boundaries)
        assert [x["after_page"] for x in boundaries if x["decision"] == "cut"] == [2, 4]
        assert "Würde Vorder- und Rückseite eines Blatts trennen" in b[1]["reasons"]
        assert b[1]["decision"] == "none"

    def test_duplex_penalty_only_after_odd_pages(self):
        pages = [page(n) for n in range(1, 5)]
        boundaries, _ = score_boundaries(pages, duplex_conf=1.0)
        b = by_page(boundaries)
        assert b[1]["score"] == W.BASE + W.DUPLEX_SPLITS_SHEET
        assert b[2]["score"] == W.BASE
        unpenalized, _ = score_boundaries(pages, duplex_conf=0.5)
        assert by_page(unpenalized)[1]["score"] == W.BASE

    def test_boilerplate_header_does_not_make_documents_similar(self):
        # "Nachtrag" + Logo auf allen Seiten -> Boilerplate; ohne Entfernung
        # waere Jaccard 2/4 = 0,5 und "Ähnliche Kopfzeilen" wuerde feuern.
        pages = [page(n, header=(NACHTRAG, LOGO, f"x{n}")) for n in range(1, 4)]
        pages.append(page(4, header=(NACHTRAG, LOGO, "y4")))
        assert boilerplate_lines(pages) == {NACHTRAG, LOGO}
        b = by_page(score_boundaries(pages)[0])
        assert "Ähnliche Kopfzeilen" not in b[3]["reasons"]
        assert "Keine gemeinsamen Kopfzeilen" in b[3]["reasons"]

    def test_similar_headers_without_boilerplate(self):
        pages = [page(1, header=("a", "b", "c")), page(2, header=("a", "b", "d"))]
        assert "Ähnliche Kopfzeilen" in score_boundaries(pages)[0][0]["reasons"]

    def test_references_and_sender_ids(self):
        same = score_boundaries([page(1, refs=(REF_A,)), page(2, refs=(REF_A, REF_B))])[0][0]
        assert same["score"] == W.BASE + W.SAME_REFERENCE
        other = score_boundaries([page(1, refs=(REF_A,), ibans=("x",)), page(2, refs=(REF_B,), ibans=("y",))])[0][0]
        assert other["score"] == W.BASE + W.DIFFERENT_REFERENCES + W.SENDER_ID_CHANGES

    def test_date_gap_format_change_and_margin_code(self):
        a = page(1, dates=("2026-01-02",), margin=(3, 3))
        b = page(2, dates=("2026-03-15",), w=420.0, margin=(1, 5))
        bd = score_boundaries([a, b])[0][0]
        assert bd["score"] == W.BASE + W.DATE_GAP + W.FORMAT_CHANGE + W.MARGIN_CODE_CHANGES
        # Hoehe allein ist kein Signal (Einzug misst jedes Blatt)
        assert score_boundaries([page(1, h=835.0), page(2, h=844.0)])[0][0]["score"] == W.BASE
        # Ausrichtung wechselt (Querformat ueber /Rotate)
        assert "Seitenformat wechselt" in score_boundaries([page(1), page(2, rot=90)])[0][0]["reasons"]

    @pytest.mark.parametrize(("score", "decision"), [
        (1.4, "cut"),        # p = 0,802
        (1.3, "uncertain"),  # p = 0,786
        (-1.0, "uncertain"),  # p = 0,269
        (-1.1, "none"),      # p = 0,250
    ])
    def test_thresholds(self, score, decision):
        p = sigmoid(score)
        expected = "cut" if p >= W.P_CUT else "none" if p <= W.P_NO_CUT else "uncertain"
        assert expected == decision

    def test_address_alone_is_uncertain_without_counter(self):
        bd = score_boundaries([page(1), page(2, addr=True)])[0][0]
        assert bd["decision"] == "uncertain"  # im Zweifel Pruefung

    def test_score_without_counter_kept_for_llm_pass(self):
        # Neue Anschrift + Anrede mitten in einem fortlaufenden Zaehler
        bd = score_boundaries([page(1, counter=(4, 9)), page(2, counter=(5, 9), addr=True, salut=True)])[0][0]
        assert bd["decision"] == "none"
        assert bd["score_without_counter"] >= 0

    def test_no_cuts_from_partial_from(self):
        pages = [page(1), page(2), page(3, source="skipped"), page(4, source="skipped", w=420)]
        boundaries, _ = score_boundaries(pages, partial_from=3)
        b = by_page(boundaries)
        assert b[2]["decision"] == b[3]["decision"] == "none"
        assert b[3]["p"] is None and b[3]["reasons"] == ["Ab Seite 3 ohne Texterkennung"]
        assert b[1]["score"] == W.BASE

    def test_leading_and_trailing_blank_pages(self):
        boundaries, kinds = score_boundaries([page(1, blank=True), page(2), page(3, blank=True)])
        assert [b["decision"] for b in boundaries] == ["none", "none"]
        assert kinds == {}


class TestDuplex:
    def test_uniform_heights_are_not_duplex(self):
        # digitale PDFs / Flachbett: alle Seiten gleich hoch
        assert detect_duplex([page(n, h=842.0) for n in range(1, 9)]) == (False, 0.0)

    def test_too_few_pages(self):
        assert detect_duplex([page(1), page(2)]) == (False, 0.0)

    def test_simplex_feed(self):
        heights = [836.0, 843.0, 839.0, 841.5, 837.0, 844.0]
        duplex, conf = detect_duplex([page(n, h=h) for n, h in enumerate(heights, start=1)])
        assert duplex is False and conf < 0.8


# ---------------------------------------------------------------------------
# Merkmale aus Text
# ---------------------------------------------------------------------------

LETTER = """Beispiel Versand GmbH · Musterweg 1 · 10115 Berlin
Frau                                   Datum 01.03.2026
Erika Musterfrau
Hauptstraße 5
12345 Beispielstadt
Ihr Zeichen
Kundennummer: 47 11 0815
Betreff Ihre Anfrage
Sehr geehrte Frau Musterfrau,
vielen Dank für Ihre Anfrage.
Wir melden uns in Kürze.
Mit freundlichen Grüßen
Beispiel Versand GmbH
IBAN DE89 3704 0044 0532 0130 00
Seite 1 von 2"""


class TestExtractFeatures:
    def test_letter_first_page(self):
        sig = extract_features(LETTER)
        assert sig["counter"] == [1, 2]
        assert sig["addr"] is True and sig["salut"] is True
        assert sig["dates"] == ["2026-03-01"]
        assert len(sig["refs"]) == 1 and len(sig["ibans"]) == 1
        assert sig["margin"] is None
        # nur Hashes, kein Klartext
        flat = repr(sig)
        assert "0815" not in flat and "DE89" not in flat and "Musterfrau" not in flat
        assert all(len(h) == 12 for h in sig["refs"] + sig["ibans"] + sig["header"])

    def test_reference_hash_ignores_formatting(self):
        a, _ = parse_refs(["Vertragsnummer: 12.345-678"])
        b, _ = parse_refs(["Vertrags-Nr. 12 345 678 vom 1.2."])
        assert a == b and len(a) == 1

    def test_invalid_iban_is_ignored(self):
        _, sender = parse_refs(["IBAN DE00 3704 0044 0532 0130 00"])
        assert sender == []

    def test_ust_id(self):
        _, sender = parse_refs(["USt-IdNr.: DE123456789"])
        assert len(sender) == 1

    @pytest.mark.parametrize(("lines", "expected"), [
        (["Seite 3 von 7"], [3, 7]),
        (["Seite 3/7"], [3, 7]),
        (["Text", "Seite 12"], [12, None]),
        (["siehe Seite 5", "Seite 2 von 4"], [2, 4]),  # Angabe mit Gesamtzahl hat Vorrang
        (["Seite 9 von 3"], None),
        ([*["Inhalt"] * 9, "2 / 5"], [2, 5]),  # "x / y" nur unten
        (["2 / 5", *["Inhalt"] * 9], None),
        ([], None),
    ])
    def test_parse_counter(self, lines, expected):
        assert parse_counter(lines) == expected

    def test_address_needs_zip_line_at_top(self):
        lines = ["Kopf", "12345 Beispielstadt", *["Inhalt"] * 10]
        assert detect_address_block(lines) == (True, False)
        assert detect_address_block([*["Inhalt"] * 10, "12345 Beispielstadt"]) == (False, False)
        assert detect_address_block(["Briefkopf · 12345 Beispielstadt"]) == (False, False)

    @pytest.mark.parametrize(("text", "expected"), [
        ("x9.40.34/1/44/ ..", [1, 44]),
        ("../2 1/44/ ..", [21, 44]),
        ("/4/4.4/", [4, 44]),
        ("0011/0022 2043", None),
        ("/50/44/", None),
        (None, None),
    ])
    def test_margin_code(self, text, expected):
        assert parse_margin_code(text) == expected

    def test_garbage_lines_are_dropped(self):
        assert text_lines("a\n\n|\n  Rechnung  Nr  \nx.") == ["Rechnung Nr"]

    def test_header_digits_normalized(self):
        a = extract_features("Beispiel AG Seite 1\nInhalt")["header"]
        b = extract_features("Beispiel AG Seite 2\nInhalt")["header"]
        assert a == b
