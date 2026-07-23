class RekordboxAccessError(Exception):
    """Base class for errors reading the local Rekordbox library."""


class DbNotFound(RekordboxAccessError):
    """No Rekordbox 6/7 master.db could be located on this machine."""


class DbLocked(RekordboxAccessError):
    """The database file exists but couldn't be read (e.g. locked by another process)."""


class KeyExtractionFailed(RekordboxAccessError):
    """The SQLCipher key for master.db couldn't be recovered automatically.

    Known to happen on Rekordbox >= 6.6.5 without a key cached from an older install.
    See https://pyrekordbox.readthedocs.io/en/stable/using_pyrekordbox/database.html
    for how to supply a key manually via configuration.
    """


class UnsupportedRekordboxVersion(RekordboxAccessError):
    """The installed Rekordbox version isn't one pyrekordbox knows how to read."""
