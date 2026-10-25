"""Tag-Entscheidungen des Benutzers — eine Definition fuer Aufteilen und Neu-Analyse.

Als vom Benutzer vergeben gilt ein Tag des Dokuments, wenn er selbst angelegt
wurde (``is_auto_generated=False``) oder sein letztes Ereignis am Dokument ein
``TAG_ADDED`` ist. Als vom Benutzer entfernt gilt ein Tag, dessen letztes
Ereignis ein ``TAG_REMOVED`` ist.
"""

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditLog
from app.models.document import DocumentTag, Tag

# Obergrenze der IN-Liste pro Abfrage (SQLite-Variablenlimit).
_CHUNK = 500


@dataclass
class TagDecisions:
    """Normalisierte Tag-Namen: vom Benutzer vergeben (am Dokument) bzw. entfernt."""

    added: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)


def chunked(ids: list[str]):
    """IN-Listen in Stuecken von hoechstens _CHUNK Eintraegen."""
    for start in range(0, len(ids), _CHUNK):
        yield ids[start:start + _CHUNK]


def _tag_name(details: str | None) -> str:
    try:
        return str(json.loads(details or "{}").get("tag") or "").strip().lower()
    except (TypeError, ValueError, AttributeError):
        return ""


async def load_tag_decisions(session: AsyncSession, document_ids: list[str]) -> dict[str, TagDecisions]:
    """Tag-Entscheidungen mehrerer Dokumente mit zwei Abfragen je 500 Dokumente."""
    ids = list(dict.fromkeys(document_ids))
    last: dict[str, dict[str, str]] = {doc_id: {} for doc_id in ids}
    current: dict[str, list[tuple[str, bool]]] = {doc_id: [] for doc_id in ids}
    for chunk in chunked(ids):
        events = await session.execute(
            select(AuditLog.document_id, AuditLog.action, AuditLog.details)
            .where(AuditLog.document_id.in_(chunk))
            .where(AuditLog.action.in_([AuditAction.TAG_ADDED, AuditAction.TAG_REMOVED]))
            .order_by(AuditLog.id)
        )
        for doc_id, action, details in events.all():
            name = _tag_name(details)
            if name:
                last[doc_id][name] = action
        tags = await session.execute(
            select(DocumentTag.document_id, Tag.name, Tag.is_auto_generated)
            .join(Tag, Tag.id == DocumentTag.tag_id)
            .where(DocumentTag.document_id.in_(chunk))
        )
        for doc_id, name, auto in tags.all():
            current[doc_id].append((name, auto))

    decisions = {}
    for doc_id in ids:
        events = last[doc_id]
        decisions[doc_id] = TagDecisions(
            added={
                name for name, auto in current[doc_id]
                if not auto or events.get(name) == AuditAction.TAG_ADDED
            },
            removed={name for name, action in events.items() if action == AuditAction.TAG_REMOVED},
        )
    return decisions


async def manual_tag_names(session: AsyncSession, document_id: str) -> list[str]:
    """Vom Benutzer vergebene Tags eines Dokuments (sortiert)."""
    return sorted((await load_tag_decisions(session, [document_id]))[document_id].added)
