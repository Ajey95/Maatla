"""Small durable store. Sources and validity are kept beside every client fact."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .knowledge import is_current


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS clients (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
                    agent_hash TEXT NOT NULL, buyer_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL, ended_at TEXT
                );
                CREATE TABLE IF NOT EXISTS utterances (
                    id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id),
                    role TEXT NOT NULL, text TEXT NOT NULL, meaning TEXT,
                    language TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
                    field TEXT NOT NULL, value TEXT NOT NULL, source_kind TEXT NOT NULL,
                    source_id TEXT, observed_at TEXT NOT NULL, valid_from TEXT NOT NULL,
                    valid_to TEXT, approved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending_facts (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
                    room_id TEXT NOT NULL REFERENCES rooms(id), field TEXT NOT NULL,
                    value TEXT NOT NULL, source_utterance_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commitments (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
                    room_id TEXT NOT NULL REFERENCES rooms(id), text TEXT NOT NULL,
                    due_at TEXT, owner TEXT NOT NULL, source_utterance_id TEXT,
                    status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
                    url TEXT, source_kind TEXT NOT NULL, approved INTEGER NOT NULL,
                    updated_at TEXT NOT NULL, expires_at TEXT
                );
                CREATE TABLE IF NOT EXISTS suggestions (
                    id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id),
                    utterance_id TEXT NOT NULL REFERENCES utterances(id),
                    headline TEXT NOT NULL, why TEXT NOT NULL, reply_hindi TEXT NOT NULL,
                    evidence_json TEXT NOT NULL, provider TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            self.db.commit()

    def rows(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(x) for x in self.db.execute(sql, args).fetchall()]

    def execute(self, sql: str, args=()) -> None:
        with self.lock:
            self.db.execute(sql, args)
            self.db.commit()

    def seed(self) -> None:
        """Clearly labelled sample records, never asserted as real property research."""
        if not self.rows("SELECT id FROM clients WHERE id='sample-client'"):
            self.execute("INSERT INTO clients VALUES (?,?,?,?)", ("sample-client", "Amit Sharma (sample)", None, now()))
        if not self.rows("SELECT id FROM facts WHERE client_id='sample-client' AND field='budget'"):
            self.add_fact("sample-client", "budget", "₹80 lakh", "sample_prior_call", None)
            self.add_fact("sample-client", "road_access", "Asked about road access", "sample_prior_call", None)
        if not self.rows("SELECT id FROM knowledge WHERE id='sample-plot'"):
            self.execute("INSERT INTO knowledge VALUES (?,?,?,?,?,?,?,?)", (
                "sample-plot", "Sample seller listing — Hyderabad outskirts",
                "Illustrative listing only: 200 sq yd plot, asking price ₹85 lakh. Road access and legal documentation have not been independently verified. Never describe title, registration, zoning or availability as verified.",
                None, "sample_seller_supplied", 1, now(), None))

    def create_client(self, name: str, phone: str | None = None) -> dict:
        cid = "client-" + uuid4().hex[:12]
        self.execute("INSERT INTO clients VALUES (?,?,?,?)", (cid, name.strip(), phone, now()))
        return self.rows("SELECT * FROM clients WHERE id=?", (cid,))[0]

    def create_room(self, client_id: str) -> dict:
        rid = "call-" + uuid4().hex[:12]
        agent_token, buyer_token = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        digest = lambda s: hashlib.sha256(s.encode()).hexdigest()
        self.execute("INSERT INTO rooms VALUES (?,?,?,?,?,NULL)",
                     (rid, client_id, digest(agent_token), digest(buyer_token), now()))
        return {"id": rid, "agent_token": agent_token, "buyer_token": buyer_token, "client_id": client_id}

    def role(self, room_id: str, token: str) -> str | None:
        row = self.rows("SELECT agent_hash,buyer_hash FROM rooms WHERE id=?", (room_id,))
        if not row or not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        if secrets.compare_digest(digest, row[0]["agent_hash"]):
            return "agent"
        if secrets.compare_digest(digest, row[0]["buyer_hash"]):
            return "buyer"
        return None

    def room(self, room_id: str) -> dict | None:
        rows = self.rows("SELECT id,client_id,created_at,ended_at FROM rooms WHERE id=?", (room_id,))
        return rows[0] if rows else None

    def add_utterance(self, room_id: str, role: str, text: str, language: str | None, origin: str) -> dict:
        uid = "utt-" + uuid4().hex[:12]
        self.execute("INSERT INTO utterances VALUES (?,?,?,?,?,?,?,?)",
                     (uid, room_id, role, text.strip(), None, language, origin, now()))
        return self.rows("SELECT * FROM utterances WHERE id=?", (uid,))[0]

    def set_meaning(self, utterance_id: str, meaning: str) -> None:
        self.execute("UPDATE utterances SET meaning=? WHERE id=?", (meaning, utterance_id))

    def add_suggestion(self, room_id: str, utterance_id: str, headline: str, why: str,
                       reply_hindi: str, evidence: list[dict], provider: str) -> dict:
        sid = "sug-" + uuid4().hex[:12]
        self.execute("INSERT INTO suggestions VALUES (?,?,?,?,?,?,?,?,?)",
                     (sid, room_id, utterance_id, headline, why, reply_hindi,
                      json.dumps(evidence, ensure_ascii=False), provider, now()))
        row = self.rows("SELECT * FROM suggestions WHERE id=?", (sid,))[0]
        row["evidence"] = json.loads(row.pop("evidence_json"))
        return row

    def add_pending_fact(self, client_id: str, room_id: str, field: str, value: str, source_id: str) -> dict:
        fid = "pending-" + uuid4().hex[:12]
        self.execute("INSERT INTO pending_facts VALUES (?,?,?,?,?,?,?,?)",
                     (fid, client_id, room_id, field, value, source_id, "pending", now()))
        return self.rows("SELECT * FROM pending_facts WHERE id=?", (fid,))[0]

    def add_fact(self, client_id: str, field: str, value: str, source_kind: str,
                 source_id: str | None, observed_at: str | None = None) -> dict:
        stamp, fid = observed_at or now(), "fact-" + uuid4().hex[:12]
        with self.lock:
            previous = self.db.execute(
                "SELECT id,valid_to FROM facts WHERE client_id=? AND field=? AND valid_from<=? "
                "ORDER BY valid_from DESC, approved_at DESC LIMIT 1", (client_id, field, stamp)).fetchone()
            following = self.db.execute(
                "SELECT valid_from FROM facts WHERE client_id=? AND field=? AND valid_from>? "
                "ORDER BY valid_from ASC LIMIT 1", (client_id, field, stamp)).fetchone()
            valid_to = following["valid_from"] if following else None
            if previous and (previous["valid_to"] is None or previous["valid_to"] > stamp):
                self.db.execute("UPDATE facts SET valid_to=? WHERE id=?", (stamp, previous["id"]))
            self.db.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (fid, client_id, field, value, source_kind, source_id, stamp, stamp, valid_to, now()))
            self.db.commit()
        return self.rows("SELECT * FROM facts WHERE id=?", (fid,))[0]

    def approve_pending(self, room_id: str, ids: list[str]) -> list[dict]:
        approved = []
        for fid in ids:
            rows = self.rows("SELECT * FROM pending_facts WHERE id=? AND room_id=? AND status='pending'", (fid, room_id))
            if not rows:
                continue
            p = rows[0]
            source = self.rows("SELECT created_at FROM utterances WHERE id=?", (p["source_utterance_id"],))
            approved.append(self.add_fact(p["client_id"], p["field"], p["value"],
                                          "client_statement", p["source_utterance_id"],
                                          source[0]["created_at"] if source else None))
            if p["field"] == "site_visit":
                self.add_commitment(p["client_id"], room_id, "Site visit: " + p["value"],
                                    "agent", p["source_utterance_id"])
            self.execute("UPDATE pending_facts SET status='approved' WHERE id=?", (fid,))
        return approved

    def reject_pending(self, room_id: str, ids: list[str]) -> None:
        for fid in ids:
            self.execute("UPDATE pending_facts SET status='rejected' WHERE id=? AND room_id=? AND status='pending'",
                         (fid, room_id))

    def add_commitment(self, client_id: str, room_id: str, text: str, owner: str,
                       source_utterance_id: str | None = None, due_at: str | None = None) -> dict:
        cid = "commit-" + uuid4().hex[:12]
        self.execute("INSERT INTO commitments VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, client_id, room_id, text, due_at, owner, source_utterance_id, "open", now()))
        return self.rows("SELECT * FROM commitments WHERE id=?", (cid,))[0]

    def summary(self, room_id: str) -> dict:
        room = self.room(room_id)
        if not room:
            raise KeyError(room_id)
        utterances = self.rows("SELECT * FROM utterances WHERE room_id=? ORDER BY created_at", (room_id,))
        brief = self.brief(room["client_id"])
        active = {x["field"]: x for x in brief["current"]}
        pending = self.rows("SELECT * FROM pending_facts WHERE room_id=? AND status='pending'", (room_id,))
        return {
            "room_id": room_id,
            "started_at": room["created_at"], "ended_at": room["ended_at"],
            "buyer_requirements": [{"field": key, "value": active[key]["value"],
                                    "source_id": active[key]["source_id"], "observed_at": active[key]["observed_at"]}
                                   for key in ("budget", "area", "plot_size", "intended_use", "decision_maker") if key in active],
            "concerns": [{"value": active["concern"]["value"], "source_id": active["concern"]["source_id"]}]
                        if "concern" in active else [],
            "site_visit": active["site_visit"]["value"] if "site_visit" in active else None,
            "commitments": brief["commitments"],
            "pending_review": pending,
            "conversation_excerpt": [{"role": x["role"], "text": x["text"], "utterance_id": x["id"]}
                                     for x in utterances[-8:]],
            "note": "Extractive summary. Only agent-approved facts are promoted to current client memory.",
        }

    def brief(self, client_id: str) -> dict:
        current = self.rows("SELECT * FROM facts WHERE client_id=? AND valid_to IS NULL ORDER BY observed_at DESC", (client_id,))
        history = self.rows("SELECT * FROM facts WHERE client_id=? ORDER BY observed_at DESC LIMIT 30", (client_id,))
        commitments = self.rows("SELECT * FROM commitments WHERE client_id=? AND status='open' ORDER BY created_at DESC", (client_id,))
        recent = self.rows("SELECT u.* FROM utterances u JOIN rooms r ON r.id=u.room_id WHERE r.client_id=? ORDER BY u.created_at DESC LIMIT 12", (client_id,))
        return {"current": current, "history": history, "commitments": commitments, "recent_utterances": recent}

    def state(self, room_id: str, role: str) -> dict:
        room = self.room(room_id)
        if not room:
            raise KeyError(room_id)
        client = self.rows("SELECT id,name FROM clients WHERE id=?", (room["client_id"],))[0]
        utterances = self.rows("SELECT * FROM utterances WHERE room_id=? ORDER BY created_at", (room_id,)) if role == "agent" else self.rows(
            "SELECT * FROM utterances WHERE room_id=? AND (role='buyer' OR origin='approved_tts') ORDER BY created_at", (room_id,))
        base = {"room": room, "client": client, "utterances": utterances}
        if role == "agent":
            suggestions = self.rows("SELECT * FROM suggestions WHERE room_id=? ORDER BY created_at DESC LIMIT 10", (room_id,))
            for s in suggestions:
                s["evidence"] = json.loads(s.pop("evidence_json"))
            base.update({"brief": self.brief(room["client_id"]),
                         "pending_facts": self.rows("SELECT * FROM pending_facts WHERE room_id=? AND status='pending'", (room_id,)),
                         "suggestions": suggestions,
                         "knowledge": [x for x in self.rows("SELECT * FROM knowledge WHERE approved=1 ORDER BY updated_at DESC")
                                       if is_current(x)]})
        return base
