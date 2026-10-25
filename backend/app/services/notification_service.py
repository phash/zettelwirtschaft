"""Benachrichtigungen fuer die Glocke im Header (ausser Garantie-Erinnerungen).

Der Queue-Worker ruft diese Helfer auf, wenn ein Dokument eine Pruefung
braucht (REVIEW_NEEDED) oder eine Verarbeitung fehlschlaegt (SYSTEM), der
E-Mail-Import bei relevanten Mails ohne verwertbares Dokument (SYSTEM), der
Massenlauf der Neu-Analyse an seinem Ende (REANALYSIS_DONE). Die
Helfer fuegen die Notification nur zur Session hinzu — committen muss der
Aufrufer (in derselben kurzen Transaktion wie die Statusaenderung).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType

_MAX_ERROR_LEN = 500


def notify_review_needed(
    session: AsyncSession,
    document_id: str,
    title: str,
    open_questions: int = 0,
) -> Notification:
    if open_questions == 1:
        detail = "Die KI hat eine Rückfrage."
    elif open_questions > 1:
        detail = f"Die KI hat {open_questions} Rückfragen."
    else:
        detail = "Die KI war sich bei der Analyse unsicher."
    notification = Notification(
        type=NotificationType.REVIEW_NEEDED,
        title=f"Bitte prüfen: {title}",
        message=f"{detail} Bitte prüfen Sie das Dokument unter „Zu prüfen“.",
        document_id=document_id,
    )
    session.add(notification)
    return notification


def notify_processing_failed(
    session: AsyncSession,
    filename: str,
    error: str | None,
    document_id: str | None = None,
) -> Notification:
    reason = (error or "Unbekannter Fehler").strip()
    if len(reason) > _MAX_ERROR_LEN:
        reason = reason[: _MAX_ERROR_LEN - 1] + "…"
    return notify_system(
        session,
        f"Verarbeitung fehlgeschlagen: {filename}",
        f"Die Datei „{filename}“ konnte nicht verarbeitet werden: {reason}",
        document_id=document_id,
    )


def notify_system(
    session: AsyncSession,
    title: str,
    message: str,
    document_id: str | None = None,
) -> Notification:
    """Allgemeine SYSTEM-Benachrichtigung (Fehler, die der Benutzer sehen soll)."""
    notification = Notification(
        type=NotificationType.SYSTEM,
        title=title,
        message=message,
        document_id=document_id,
    )
    session.add(notification)
    return notification


def notify_split_needed(
    session: AsyncSession,
    filename: str,
    *,
    parts: int,
    uncertain: bool,
) -> Notification:
    """Stapel-PDF wartet im Aufteilungs-Editor (SPLIT_NEEDED, ohne Dokument —
    das Frontend leitet auf /pruefen). ``parts``: vorgeschlagene Teilezahl."""
    if parts >= 2:
        message = f"„{filename}“ enthält vermutlich {parts} Dokumente – bitte prüfen."
    elif uncertain:
        message = f"„{filename}“ enthält möglicherweise mehrere Dokumente – bitte prüfen."
    else:
        message = f"Die Seiten von „{filename}“ sind erkannt – bitte aufteilen."
    notification = Notification(
        type=NotificationType.SPLIT_NEEDED,
        title="Stapel aufteilen",
        message=message,
        document_id=None,
    )
    session.add(notification)
    return notification


def notify_reanalysis_done(
    session: AsyncSession,
    *,
    proposed: int,
    unchanged: int,
    failed: int,
) -> Notification:
    """Massenlauf der Neu-Analyse ist fertig (REANALYSIS_DONE, ohne Dokument —
    das Frontend verlinkt die Vorschlagsliste)."""
    notification = Notification(
        type=NotificationType.REANALYSIS_DONE,
        title="Neu-Analyse fertig",
        message=f"{proposed} Vorschläge, {unchanged} unverändert, {failed} Fehler",
        document_id=None,
    )
    session.add(notification)
    return notification
