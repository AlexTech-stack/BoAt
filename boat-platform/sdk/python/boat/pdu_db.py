"""PDU database loader.

Loads a pdu_db.json file (see config/pdu_db.schema.json) and provides
lookup by DbId or MessageName.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class PduDatabase:
    def __init__(self, path: str | Path) -> None:
        with open(path) as f:
            raw = json.load(f)
        self._messages: Dict[int, dict]     = {}
        self._by_name:  Dict[str, dict]     = {}
        self._routes:   List[dict]          = raw.get("signal_routes", [])

        for msg in raw.get("messages", []):
            db_id = msg["DbId"]
            name  = msg["MessageName"]
            self._messages[db_id] = msg
            self._by_name[name]   = msg

    # ------------------------------------------------------------------

    def by_id(self, db_id: int) -> Optional[dict]:
        return self._messages.get(db_id)

    def by_name(self, name: str) -> Optional[dict]:
        return self._by_name.get(name)

    def signal_routes(self) -> List[dict]:
        return list(self._routes)

    def names(self) -> List[str]:
        return list(self._by_name.keys())
