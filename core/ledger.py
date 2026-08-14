"""Hash-chained, append-only audit ledger (PRD 5.6).

Every action in the system — a chat prompt, a retrieval decision, an intent parse,
a data read, a rule evaluation, a math step, a citation, an authoring approval —
appends one event here. Each event stores the hash of the prior event, so any edit
or deletion breaks the chain and is caught by `verify()`. This is the record that
must survive union/council/legal scrutiny.

TAMPER MODEL. A plain hash chain only proves internal consistency: an attacker with
disk access can rewrite event N and recompute every hash after it, or truncate the
tail, and the chain still verifies. Two mechanisms close that gap:

  1. HMAC. With KENNY_LEDGER_KEY set (a secret that must NOT live on the same volume
     as the ledger — it belongs in the platform's secret store), every event hash is
     HMAC-SHA256 under that key. Rewriting the chain now requires the key, and
     `verify()` refuses any event that was not keyed.
  2. Head anchoring. When keyed, every append emits `KENNY_LEDGER_HEAD seq=N hash=…`
     to stdout, so the platform's log retention holds an external record of the head.
     `verify_anchor(seq, hash)` checks a recorded head against the current file —
     a truncated tail no longer contains the anchored event and fails.

Without the key the ledger degrades to the old integrity-only chain (local dev).

Storage is append-only JSONL (one event per line). The scale roadmap (PRD §8)
swaps this for SQLite/Postgres behind the same append()/read()/verify() surface.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
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
KEY_ENV = "KENNY_LEDGER_KEY"


def _key() -> bytes | None:
    """The HMAC key, read per call so tests and rotation see the live environment."""
    val = os.environ.get(KEY_ENV, "").strip()
    return val.encode("utf-8") if val else None

# A hash chain is read-then-append: read the last hash, link to it, write. Two writers
# interleaving produce duplicate seq numbers and a broken chain — i.e. two people asking
# a question at the same moment destroys the audit record. Serialise writes with an
# in-process lock (threads: FastAPI runs sync endpoints in a threadpool) AND an advisory
# file lock (processes: more than one uvicorn worker).
_APPEND_LOCK = threading.Lock()


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_hash: str, seq: int, ts: float, actor: str, type_: str, payload: Any,
          key: bytes | None = None) -> str:
    material = f"{prev_hash}|{seq}|{ts}|{actor}|{type_}|{_canonical(payload)}"
    if key is not None:
        return _hmac.new(key, material.encode("utf-8"), hashlib.sha256).hexdigest()
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
        key = _key()
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
                        "alg": "hmac-sha256" if key is not None else "sha256",
                        "hash": _hash(prev_hash, seq, ts, actor, type_, payload, key),
                    }
                    f.write(_canonical(event) + "\n")
                    f.flush()
                    os.fsync(f.fileno())         # the record must survive a crash
                finally:
                    if fcntl is not None:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        if key is not None:
            # External anchor: platform log retention holds the head outside the volume,
            # so a truncated file can be caught against the last anchored head.
            print(f"KENNY_LEDGER_HEAD seq={event['seq']} hash={event['hash']}", flush=True)
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
        """Recompute the whole chain.

        With KENNY_LEDGER_KEY set, every event MUST verify under the HMAC — an event
        hashed without the key fails, because accepting unkeyed events would let an
        attacker rewrite the file as a plain (keyless) chain and pass. A ledger written
        before the key was configured therefore fails verification once the key is on:
        archive it (export, then reseed) rather than mixing keyed and unkeyed history.
        """
        key = _key()
        prev_hash = GENESIS
        expected_seq = 0
        for ev in self.read():
            if ev["seq"] != expected_seq:
                return False, f"seq gap at {ev['seq']} (expected {expected_seq})"
            if ev["prev_hash"] != prev_hash:
                return False, f"broken chain at seq {ev['seq']}: prev_hash mismatch"
            if key is not None and ev.get("alg") != "hmac-sha256":
                return False, (f"unkeyed event at seq {ev['seq']}: KENNY_LEDGER_KEY is "
                               f"set but this event predates it (or was rewritten "
                               f"without the key). Archive the old ledger or unset the key.")
            recomputed = _hash(prev_hash, ev["seq"], ev["ts"], ev["actor"],
                               ev["type"], ev["payload"], key)
            if recomputed != ev["hash"]:
                return False, f"tampered event at seq {ev['seq']}: hash mismatch"
            prev_hash = ev["hash"]
            expected_seq += 1
        return True, "chain intact"

    def head(self) -> dict | None:
        """The current chain head: {"seq", "hash", "count"}. Record it externally (it is
        also printed on every keyed append) to make truncation detectable later."""
        last = self._last()
        if last is None:
            return None
        return {"seq": last["seq"], "hash": last["hash"], "count": last["seq"] + 1}

    def verify_anchor(self, seq: int, hash_: str) -> tuple[bool, str]:
        """Check a previously-recorded head against the current file.

        The chain must still verify AND the anchored event must still exist with the
        same hash. A tail truncated past the anchor, or a rewrite that changed history
        at or before it, both fail — this is the check `verify()` alone cannot make.
        """
        ok, msg = self.verify()
        if not ok:
            return False, msg
        for ev in self.read():
            if ev["seq"] == seq:
                if ev["hash"] == hash_:
                    return True, f"anchor at seq {seq} intact"
                return False, f"anchor mismatch at seq {seq}: history was rewritten"
        return False, (f"anchored seq {seq} is missing: the ledger was truncated "
                       f"below the recorded head")

    def export(self) -> str:
        """Full ledger plus a header line binding the export to its head. A saved export
        is an anchor: verify_anchor(header.head.seq, header.head.hash) later proves the
        live file still contains everything this export saw."""
        events = list(self.read())
        header = {"export": {
            "count": len(events),
            "head": ({"seq": events[-1]["seq"], "hash": events[-1]["hash"]}
                     if events else None),
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "keyed": _key() is not None,
        }}
        return "\n".join([_canonical(header)] + [_canonical(e) for e in events])
