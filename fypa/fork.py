# Copyright (c) 2026 Tomachie LLC.
"""This fork of FYPA: the tag it adds to the version it is built on, and who changed it.

``fypa.cli.__version__`` stays the original project's (its release workflow stamps it);
what FYPA shows - ``--version``, About, report headers - is :func:`version`, that version
with this fork's tag as a local version ("0.1.0-dev+tomachie.1"). Raise the tag's number
with every change this fork ships.
"""

TAG = "tomachie.1"
BY = "Tomachie LLC"
REPO = "https://github.com/Tomachie/FYPA"


def version() -> str:
    from fypa.cli import __version__
    return f"{__version__}+{TAG}"
