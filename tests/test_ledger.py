"""Tamper-evidence tests for the audit ledger (TICKETS.md A2).

The threat is an attacker WITH disk access: they can rewrite any event and recompute
every hash after it, or delete the tail. A plain hash chain passes both. The HMAC key
(held off-volume) defeats the rewrite; an externally-recorded head defeats truncation.
"""
import hashlib
import json

import pytest

from core import ledger as ledger_mod
from core.ledger import GENESIS, Ledger


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv(ledger_mod.KEY_ENV, "test-secret-key")


def _make(tmp_path, n=3):
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    for i in range(n):
        led.append("event", {"i": i}, actor="test")
    return led


def _rows(led):
    with open(led.path) as f:
        return [json.loads(l) for l in f if l.strip()]


def _write(led, rows):
    with open(led.path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")


def _plain_hash(prev_hash, ev):
    material = (f"{prev_hash}|{ev['seq']}|{ev['ts']}|{ev['actor']}|{ev['type']}|"
                + json.dumps(ev["payload"], sort_keys=True, separators=(",", ":"),
                             default=str))
    return hashlib.sha256(material.encode()).hexdigest()


def test_plain_mode_still_verifies(tmp_path):
    led = _make(tmp_path)
    ok, msg = led.verify()
    assert ok, msg


def test_keyed_chain_verifies(tmp_path, keyed):
    led = _make(tmp_path)
    ok, msg = led.verify()
    assert ok, msg
    assert all(r["alg"] == "hmac-sha256" for r in _rows(led))


def test_rewrite_with_recompute_fails_without_key(tmp_path, keyed):
    """The attack verify() used to miss: edit event 1, recompute hashes 1..tail.
    The attacker has the disk but not HOLLY_LEDGER_KEY, so the best they can do is a
    plain-SHA256 rechain — which verify() must reject."""
    led = _make(tmp_path, n=4)
    rows = _rows(led)
    rows[1]["payload"] = {"i": "FORGED"}
    prev = rows[0]["hash"]
    for ev in rows[1:]:
        ev["prev_hash"] = prev
        ev["alg"] = "sha256"          # attacker can't produce HMAC values
        ev["hash"] = _plain_hash(prev, ev)
        prev = ev["hash"]
    _write(led, rows)
    ok, msg = led.verify()
    assert not ok
    assert "unkeyed" in msg or "hash mismatch" in msg


def test_forged_hmac_without_key_fails(tmp_path, keyed):
    """Even claiming alg=hmac-sha256, the attacker can't compute valid values."""
    led = _make(tmp_path, n=3)
    rows = _rows(led)
    rows[1]["payload"] = {"i": "FORGED"}
    rows[1]["hash"] = hashlib.sha256(b"guess").hexdigest()
    _write(led, rows)
    ok, msg = led.verify()
    assert not ok


def test_truncation_passes_verify_but_fails_anchor(tmp_path, keyed):
    """Deleting the tail leaves a valid prefix — verify() alone cannot see it. The
    recorded head (seq+hash, held externally) catches it."""
    led = _make(tmp_path, n=5)
    head = led.head()
    assert head["count"] == 5
    _write(led, _rows(led)[:3])       # attacker deletes the last 2 events
    ok, _ = led.verify()
    assert ok                          # the blind spot, by construction
    ok, msg = led.verify_anchor(head["seq"], head["hash"])
    assert not ok
    assert "truncated" in msg


def test_anchor_intact_on_untouched_ledger(tmp_path, keyed):
    led = _make(tmp_path, n=4)
    head = led.head()
    led.append("later", {"more": True})
    ok, msg = led.verify_anchor(head["seq"], head["hash"])
    assert ok, msg


def test_unkeyed_history_rejected_once_key_is_set(tmp_path, monkeypatch):
    """A ledger written before the key existed must not silently pass under the key —
    that acceptance is exactly the downgrade a rewriting attacker would use."""
    monkeypatch.delenv(ledger_mod.KEY_ENV, raising=False)
    led = _make(tmp_path, n=2)
    monkeypatch.setenv(ledger_mod.KEY_ENV, "new-key")
    ok, msg = led.verify()
    assert not ok
    assert "unkeyed" in msg


def test_export_header_binds_head(tmp_path, keyed):
    led = _make(tmp_path, n=3)
    lines = led.export().splitlines()
    header = json.loads(lines[0])["export"]
    assert header["count"] == 3
    assert header["head"]["seq"] == 2
    assert header["keyed"] is True
    ok, msg = led.verify_anchor(header["head"]["seq"], header["head"]["hash"])
    assert ok, msg
