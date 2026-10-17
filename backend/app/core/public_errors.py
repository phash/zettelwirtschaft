"""Fehlertexte, die an Clients gehen (SEC-DATA-05 / S-LOW4 / S-LOW5).

``str(exc)`` unerwarteter Exceptions enthaelt bei OSError absolute Pfade
(Benutzername, Datenordner), bei SQLAlchemy SQL samt gebundenen Parametern.
Solche Texte landen in der DB (Job-/Task-Fehler) und gehen per API an den
Client — dort steht deshalb nur der Exception-Typ; die Details gehoeren ins
Server-Log (``logger.exception`` an der Fangstelle).
"""


def internal_error_message(exc: BaseException) -> str:
    """Neutraler Fehlertext fuer eine unerwartete Exception."""
    return f"Interner Fehler ({type(exc).__name__}) — Details im Server-Log."
