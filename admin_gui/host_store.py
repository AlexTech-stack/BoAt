# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Persisted list of launcher-agent hosts the admin app talks to.

No Qt dependency -- plain JSON file under the user's home directory, so it
survives across runs of the app without needing a database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

DEFAULT_PATH = Path.home() / ".boat" / "admin_hosts.json"


class HostStore:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self._hosts: List[dict] = []
        self.load()

    def load(self) -> None:
        if self.path.is_file():
            try:
                self._hosts = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._hosts = []
        else:
            self._hosts = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._hosts, indent=2))

    def list(self) -> List[dict]:
        return list(self._hosts)

    def add(self, name: str, url: str) -> None:
        url = url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        if any(h["url"] == url for h in self._hosts):
            raise ValueError(f"host {url} is already added")
        self._hosts.append({"name": name or url, "url": url})
        self.save()

    def remove(self, url: str) -> None:
        url = url.rstrip("/")
        self._hosts = [h for h in self._hosts if h["url"] != url]
        self.save()
