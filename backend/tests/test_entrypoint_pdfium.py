"""--self-test-pdfium des Native-Entrypoints (build-native.ps1 ruft ihn gegen
das fertige Bundle auf: pdfium.dll + Paketdaten muessen ladbar sein)."""

from unittest.mock import patch

from app.services.pdf_split_service import PdfSplitError


def test_self_test_pdfium_succeeds_without_config(monkeypatch, capsys):
    monkeypatch.delenv("ZETTELWIRTSCHAFT_CONFIG", raising=False)
    from app.entrypoint import main

    assert main(["--self-test-pdfium"]) == 0
    assert capsys.readouterr().out.startswith("pdfium OK (")


def test_self_test_pdfium_reports_failure(capsys):
    from app.entrypoint import main

    with patch("app.services.pdf_split_service.self_test", side_effect=PdfSplitError("pdfium.dll fehlt")):
        assert main(["--self-test-pdfium"]) == 1
    assert "FEHLER: pdfium.dll fehlt" in capsys.readouterr().err
