"""Hash-chained, append-only audit ledger (PRD 5.6).

Every action in the system — a chat prompt, a retrieval decision, an intent parse,
a data read, a rule evaluation, a math step, a citation, an authoring approval —
appends one event here. Each event stores the hash of the prior event, so any edit
or deletion breaks the chain and is caught by `verify()`. This is the record that
must survive union/council/legal scrutiny.

Storage is append-only JSONL (one event per line). The scale roadmap (PRD §8)
swaps this for SQLite/Postgres behind the same append()/read()/verify() surface.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Iterable

try:
    import fcntl  # POSIX advisory locking
except Exception:  # pragma: no cover - non-POSIX
    fcntl = None

GENESIS = "0" * 64

# A hash chain is read-then-append: read the last hash, link to it, write. Two writers
# interleaving produce duplicate seq numbers and a broken chain — i.e. two people asking
# a question at the same moment destroys the audit record. Serialise writes with an
# in-process lock (threads: FastAPI runs sync endpoints in a threadpool) AND an advisory
# file lock (processes: more than one uvicorn worker).
_APPEND_LOCK = threading.Lock()


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_hash: str, seq: int, ts: float, actor: str, type_: str, payload: Any) -> str:
    material = f"{prev_hash}|{seq}|{ts}|{actor}|{type_}|{_canonical(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # ---- write ----
    def _last(self) -> dict | None:
        last = None
        for ev in self.read():
            last = ev
        return last

    def append(self, type_: str, payload: dict, actor: str = "system",
               query_id: str | None = None) -> dict:
        """Append one event, linked to the previous one's hash.

        The whole read-link-write is atomic: concurrent appends would otherwise reuse a
        seq/prev_hash and break the chain — verified by
        tests/test_engine_dsl.py::test_ledger_survives_concurrent_writes.
        """
        with _APPEND_LOCK:                       # serialise threads in this process
            with open(self.path, "a+") as f:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)   # and other processes
                try:
                    last = self._last()
                    seq = (last["seq"] + 1) if last else 0
                    prev_hash = last["hash"] if last else GENESIS
                    ts = time.time()
                    event = {
                        "seq": seq,
                        "ts": ts,
                        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)),
                        "actor": actor,
                        "query_id": query_id,
                        "type": type_,
                        "payload": payload,
                        "prev_hash": prev_hash,
                        "hash": _hash(prev_hash, seq, ts, actor, type_, payload),
                    }
                    f.write(_canonical(event) + "\n")
                    f.flush()
                    os.fsync(f.fileno())         # the record must survive a crash
                finally:
                    if fcntl is not None:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return event

    # ---- read ----
    def read(self) -> Iterable[dict]:
        if not os.path.exists(self.path):
            return []
        events = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def for_query(self, query_id: str) -> list[dict]:
        return [e for e in self.read() if e.get("query_id") == query_id]

    # ---- integrity ----
    def verify(self) -> tuple[bool, str]:
        prev_hash = GENESIS
        expected_seq = 0
        for ev in self.read():
            if ev["seq"] != expected_seq:
                return False, f"seq gap at {ev['seq']} (expected {expected_seq})"
            if ev["prev_hash"] != prev_hash:
                return False, f"broken chain at seq {ev['seq']}: prev_hash mismatch"
            recomputed = _hash(prev_hash, ev["seq"], ev["ts"], ev["actor"],
                               ev["type"], ev["payload"])
            if recomputed != ev["hash"]:
                return False, f"tampered event at seq {ev['seq']}: hash mismatch"
            prev_hash = ev["hash"]
            expected_seq += 1
        return True, "chain intact"

    def export(self) -> str:
        return "\n".join(_canonical(e) for e in self.read())
