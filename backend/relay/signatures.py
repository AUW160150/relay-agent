"""Electronic signatures in the style of 21 CFR Part 11.

- Two distinct identification components: user ID and password (11.200(a)(1)).
- The signature manifestation carries printed name, date and time, and meaning (11.50).
- The signature is a ledger entry bound to the hash of the record it signs (11.70):
  if the record changes afterwards, verify_signature() fails.
A spoken "yes" never reaches this module; only the re-authentication dialog does.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from .ledger import Ledger, record_hash

MEANINGS = {
    "sponsor": "Approved as Sponsor Data Manager",
    "cro": "Deployment approved as CRO Study Manager",
    "site": "Transition plan acknowledged as Site Coordinator",
}
MAX_FAILED = 3


class SignatureError(Exception):
    pass


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def check_password(password: str, stored: str) -> bool:
    salt, dk = stored.split("$")
    cand = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(cand.hex(), dk)


class Signer:
    def __init__(self, ledger: Ledger, users: dict[str, dict]):
        self.ledger = ledger
        self.users = users  # user_id -> {name, role, title, password_hash}
        self.failed: dict[str, int] = {}

    def sign(self, *, session_user_id: str, user_id: str, password: str, meaning: str,
             record_type: str, record_id: str, record: object) -> dict:
        user = self.users.get(user_id)
        role = user["role"] if user else "unknown"
        if self.failed.get(user_id, 0) >= MAX_FAILED:
            raise SignatureError("This account is locked after repeated failed attempts.")
        problem = None
        if not user or not password or not check_password(password, user["password_hash"]):
            problem = "User ID or password is not correct."
        elif user_id != session_user_id:
            problem = "The signature must be made by the person who is signed in."
        elif meaning != MEANINGS.get(user["role"]):
            problem = "The signature meaning does not match this role."
        if problem:
            self.failed[user_id] = self.failed.get(user_id, 0) + 1
            self.ledger.append(actor_type="person", actor_id=session_user_id, role=role,
                               action="signature_failed", record=record_id, reason=problem,
                               extra={"attempted_user_id": user_id})
            raise SignatureError(problem)
        self.failed[user_id] = 0
        rh = record_hash(record)
        entry = self.ledger.append(
            actor_type="person", actor_id=user_id, role=user["role"], action="signature",
            record=record_id, reason=meaning,
            extra={"record_type": record_type, "signed_record_hash": rh,
                   "printed_name": user["name"], "title": user["title"], "meaning": meaning,
                   "components": ["user_id", "password"]},
        )
        return manifestation(entry)

    def verify_signature(self, seq: int, record: object) -> dict:
        """Is signature entry `seq` intact, and does it still bind to this record?"""
        entry = next((e for e in self.ledger.entries if e["seq"] == seq and e["action"] == "signature"), None)
        if not entry:
            return {"ok": False, "reason": "No such signature"}
        chain = self.ledger.verify()
        if not chain["ok"]:
            return {"ok": False, "reason": f"Ledger chain is broken at entry {chain['broken_at']}"}
        if entry["extra"]["signed_record_hash"] != record_hash(record):
            return {"ok": False, "reason": "The record was changed after it was signed"}
        return {"ok": True, **manifestation(entry)}


def manifestation(entry: dict) -> dict:
    x = entry["extra"]
    return {"seq": entry["seq"], "printed_name": x["printed_name"], "title": x["title"],
            "user_id": entry["actor_id"], "signed_at": entry["ts"], "meaning": x["meaning"],
            "record": entry["record"], "record_type": x["record_type"],
            "signed_record_hash": x["signed_record_hash"], "ledger_hash": entry["hash"]}
