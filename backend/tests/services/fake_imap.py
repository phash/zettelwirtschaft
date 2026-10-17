"""In-Memory-IMAP-Server fuer Tests des E-Mail-Abrufs.

Bildet die von ``email_fetch_service`` genutzte imaplib-Oberflaeche nach
(UID-Befehle, Flags, Ordner, Capabilities) und protokolliert jeden Befehl, damit
Tests pruefen koennen, was NICHT gesendet wurde (z.B. kein EXPUNGE nach
fehlgeschlagenem COPY).
"""

import imaplib
import re


def _unquote(name: str) -> str:
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        return name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return name


def _quote_inner(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _uids(uid_set: str) -> list[int]:
    return [int(u) for u in str(uid_set).split(",") if u]


class FakeImapServer:
    def __init__(
        self,
        *,
        capabilities=("IMAP4REV1", "UIDPLUS", "MOVE"),
        delimiter: str = "/",
        uidvalidity: str = "1",
        copy_ok: bool = True,
        create_ok: bool = True,
        login_ok: bool = True,
        folders=("INBOX",),
    ):
        self.capabilities = tuple(capabilities)
        self.delimiter = delimiter
        self.uidvalidity = uidvalidity
        self.copy_ok = copy_ok
        self.create_ok = create_ok
        self.login_ok = login_ok
        # Ordner -> {uid: {"raw": bytes, "flags": set[str]}}
        self.folders: dict[str, dict[int, dict]] = {f: {} for f in folders}
        self.commands: list[tuple] = []
        self.connections = 0
        self.on_select = None  # optionaler Hook(server, connection_no)
        self._next_uid = 1

    def add_message(self, raw: bytes, folder: str = "INBOX", flags=()) -> int:
        uid = self._next_uid
        self._next_uid += 1
        self.folders[folder][uid] = {"raw": raw, "flags": set(flags)}
        return uid

    def connection(self, *args, **kwargs) -> "FakeImapConnection":
        self.connections += 1
        return FakeImapConnection(self, self.connections)

    def command_names(self) -> list[str]:
        return [c[0] for c in self.commands]

    def uid_commands(self, name: str) -> list[tuple]:
        return [c for c in self.commands if c[0] == "UID" and c[1] == name]


class FakeImapConnection:
    def __init__(self, server: FakeImapServer, number: int):
        self.server = server
        self.number = number
        self.selected: str | None = None
        self.readonly = False
        self.capabilities = ("IMAP4REV1",)  # vor dem Login (wie imaplib)

    def _log(self, *cmd):
        self.server.commands.append(cmd)

    def login(self, user, password):
        self._log("LOGIN", user)
        if not self.server.login_ok:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"Logged in"]

    def capability(self):
        self._log("CAPABILITY")
        return "OK", [" ".join(self.server.capabilities).encode()]

    def select(self, mailbox="INBOX", readonly=False):
        name = _unquote(mailbox)
        self._log("EXAMINE" if readonly else "SELECT", name)
        if self.server.on_select:
            self.server.on_select(self.server, self.number)
        if name not in self.server.folders:
            return "NO", [b"Mailbox doesn't exist"]
        self.selected, self.readonly = name, readonly
        return "OK", [str(len(self.server.folders[name])).encode()]

    def response(self, code):
        if code == "UIDVALIDITY":
            return code, [self.server.uidvalidity.encode()]
        return code, [None]

    def list(self, directory='""', pattern="*"):
        name = _unquote(pattern)
        self._log("LIST", name)
        # Wie ein echter Server: Trenner und Name als IMAP-quoted-String.
        d = _quote_inner(self.server.delimiter)
        if name == "":
            return "OK", [f'(\\Noselect) "{d}" ""'.encode()]
        if name in self.server.folders:
            return "OK", [f'(\\HasNoChildren) "{d}" "{_quote_inner(name)}"'.encode()]
        return "OK", [None]

    def create(self, mailbox):
        name = _unquote(mailbox)
        self._log("CREATE", name)
        if not self.server.create_ok:
            return "NO", [b"Permission denied"]
        self.server.folders.setdefault(name, {})
        return "OK", [b"Created"]

    def subscribe(self, mailbox):
        self._log("SUBSCRIBE", _unquote(mailbox))
        return "OK", [b""]

    def _box(self) -> dict[int, dict]:
        assert self.selected is not None, "kein Ordner ausgewaehlt"
        return self.server.folders[self.selected]

    def uid(self, command, *args):
        command = command.upper()
        self._log("UID", command, *args)
        box = self._box()
        if command == "SEARCH":
            assert args == ("UNSEEN",), args
            uids = [u for u, m in sorted(box.items()) if "\\Seen" not in m["flags"]]
            return "OK", [" ".join(str(u) for u in uids).encode()]
        if command == "FETCH":
            uid, spec = int(args[0]), args[1]
            msg = box.get(uid)
            if msg is None:
                return "OK", [None]
            if "PEEK" not in spec:
                msg["flags"].add("\\Seen")
            return "OK", [(f"{uid} (UID {uid} BODY[] {{{len(msg['raw'])}}}".encode(), msg["raw"]), b")"]
        if command == "STORE":
            assert not self.readonly, "STORE auf read-only Ordner"
            uid_set, op, flags = args
            assert op == "+FLAGS"
            for uid in _uids(uid_set):
                if uid in box:
                    box[uid]["flags"].update(re.findall(r"\\\w+", flags))
            return "OK", [b""]
        if command in ("COPY", "MOVE"):
            uid_set, target = args
            target = _unquote(target)
            if command == "MOVE" and "MOVE" not in self.server.capabilities:
                raise imaplib.IMAP4.error("BAD unknown command")
            if target not in self.server.folders or (command == "COPY" and not self.server.copy_ok):
                return "NO", [b"[TRYCREATE] Mailbox doesn't exist"]
            for uid in _uids(uid_set):
                if uid in box:
                    copy = {"raw": box[uid]["raw"], "flags": set(box[uid]["flags"])}
                    self.server.folders[target][self.server._next_uid] = copy
                    self.server._next_uid += 1
                    if command == "MOVE":
                        del box[uid]
            return "OK", [b""]
        if command == "EXPUNGE":
            assert "UIDPLUS" in self.server.capabilities
            for uid in _uids(args[0]):
                if uid in box and "\\Deleted" in box[uid]["flags"]:
                    del box[uid]
            return "OK", [b""]
        raise AssertionError(f"Unerwarteter UID-Befehl {command}")

    def expunge(self):
        self._log("EXPUNGE")
        for uid in [u for u, m in self._box().items() if "\\Deleted" in m["flags"]]:
            del self._box()[uid]
        return "OK", [b""]

    def close(self):
        self._log("CLOSE")
        if self.selected and not self.readonly:
            self.expunge()
        self.selected = None
        return "OK", [b""]

    def logout(self):
        self._log("LOGOUT")
        return "BYE", [b""]
