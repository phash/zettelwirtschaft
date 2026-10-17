# Review- und Audit-Berichte (Archiv)

Historische Code-Reviews, Security-Audits und Analyse-Dokumente. Sie lagen bis
1.6.0 im Repo-Root und wurden per `git mv` hierher verschoben (Historie bleibt
über `git log --follow` erhalten). Die Berichte sind **Momentaufnahmen** ihres
Datums und werden nicht fortgeschrieben — der aktuelle Stand steht im Code,
in `CLAUDE.md` und im `README.md` im Repo-Root.

Code-Kommentare verweisen auf Finding-IDs in diesen Berichten (z. B.
`docs/reviews/SECURITY_AUDIT_v3.md NEW-002`).

| Datei | Datum | Inhalt | Status |
|---|---|---|---|
| [`CODE_REVIEW_FINDINGS.md`](CODE_REVIEW_FINDINGS.md) | 2026-03-29 | Erstes Code-Review (v1.2.2), 55 Findings | 47/55 gefixt, Rest in v2/v3 aufgegriffen |
| [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md) | 2026-03-29 | Erstes Security-Audit (v1.2.2) | in SECURITY_AUDIT_v2 verifiziert |
| [`CODE_REVIEW_v2.md`](CODE_REVIEW_v2.md) | 2026-05-08 | Folge-Review nach v1.2.2 (Pipeline, Performance) | umgesetzt in v1.3.0 |
| [`SECURITY_AUDIT_v2.md`](SECURITY_AUDIT_v2.md) | 2026-05-08 | Verifikation der v1.2.2-Fixes + neue Findings (N-001 … N-013) | umgesetzt; N-012-Aussage in 1.6.0 korrigiert |
| [`LIBRARY_AUDIT.md`](LIBRARY_AUDIT.md) | 2026-05-08 | Bibliotheks-Audit + Migrationsplan | Migration in v1.3.0 und 1.6.0 durchgeführt |
| [`LLM_OPTIMIZATION.md`](LLM_OPTIMIZATION.md) | 2026-05 | LLM-/OCR-/RAG-Optimierungsanalyse | Status-Block am Dateianfang (umgesetzt vs. offen) |
| [`REVIEW_REPORT.md`](REVIEW_REPORT.md) | 2026-05-08 | Sammelbericht Code/Security/Library/LLM/UI | umgesetzt in v1.3.0 |
| [`CODE_REVIEW_v3.md`](CODE_REVIEW_v3.md) | 2026-05-09 | Review neuer Patterns (Reranker, Verifier, slowapi, Lib-Bumps) | umgesetzt in v1.3.0 |
| [`SECURITY_AUDIT_v3.md`](SECURITY_AUDIT_v3.md) | 2026-05-09 | Delta-Audit (slowapi, Container-Hardening, Trusted-Proxy) | umgesetzt; Trusted-Proxy-Logik in 1.6.0 überarbeitet |
