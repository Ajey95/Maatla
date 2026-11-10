from __future__ import annotations

import asyncio
import base64
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from secrets import compare_digest
from typing import Literal
from uuid import uuid4

import httpx

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from .knowledge import fetch_authorized, find_local, is_current, search_authorized
from .providers import (GnaniSTT, prewarm_ollama, reason, synthesize_hinglish,
                        synthesize_telugu_coach, telugu_coach, telugu_live_gist, translate_reply_to_telugu,
                        translate_seller_to_hindi)
from .store import Store, now


class ClientCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str | None = None


class RoomCreate(BaseModel):
    client_id: str


class DemoTurn(BaseModel):
    role: Literal["agent", "buyer"]
    text: str = Field(min_length=1, max_length=2000)
    language: str | None = None


class FactApproval(BaseModel):
    ids: list[str]


class ManualFact(BaseModel):
    field: Literal["budget", "area", "plot_size", "intended_use", "concern", "site_visit", "decision_maker"]
    value: str = Field(min_length=1, max_length=200)
    source_utterance_id: str


class CommitmentCreate(BaseModel):
    text: str = Field(min_length=3, max_length=500)
    owner: Literal["agent", "buyer"] = "agent"
    due_at: str | None = None
    source_utterance_id: str | None = None


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=10, max_length=10000)
    url: str | None = None
    source_kind: Literal["seller_supplied"] = "seller_supplied"
    expires_at: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    fetch_url: str | None = None


class SpeakRequest(BaseModel):
    suggestion_id: str
    approved_text: str | None = Field(default=None, min_length=1, max_length=500)
    preview_id: str


class ReplyPreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class CoachVoiceRequest(BaseModel):
    utterance_id: str


class Hub:
    def __init__(self):
        self.rooms: dict[str, dict[str, WebSocket]] = {}

    async def send(self, room_id: str, role: str, event: dict):
        ws = self.rooms.get(room_id, {}).get(role)
        if ws:
            try:
                await ws.send_json(event)
            except Exception:
                self.rooms.get(room_id, {}).pop(role, None)

    async def both(self, room_id: str, event: dict):
        await asyncio.gather(self.send(room_id, "agent", event), self.send(room_id, "buyer", event))


def quick_tip(text: str) -> tuple[str, str]:
    lower = text.lower()
    if re.search(r"registr|document|title|रजिस्ट|दस्तावेज|कागज|పత్ర|రిజిస్ట్ర", lower):
        return ("Ask which land document the buyer wants checked.",
                "The buyer raised a document or registration question. Do not assert legal clearance without evidence.")
    if re.search(r"price|cost|budget|महंग|कीमत|दाम|ధర|బడ్జెట్", lower):
        return ("Ask what total price or payment range works for them.",
                "The buyer mentioned price; clarify their range before offering a concession.")
    if re.search(r"road|access|सड़क|रास्त|రోడ్|దారి", lower):
        return ("Clarify the access question and offer a map or site visit.",
                "The buyer asked about reaching the plot; use a current source for exact claims.")
    if re.search(r"visit|देख|मिल|విజిట్|చూడ", lower):
        return ("Offer a specific site-visit time and confirm it with the buyer.",
                "The buyer mentioned seeing the property; convert this into a clear next step.")
    return ("Ask one clarifying question about the buyer's latest point.",
            "Ground the next response in what the buyer just said.")


def create_app(db_path: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        warm_task = None
        if os.getenv("MAATLA_PREWARM_LLM", "true").lower() == "true":
            warm_task = asyncio.create_task(prewarm_ollama())
        yield
        if warm_task and not warm_task.done():
            warm_task.cancel()

    app = FastAPI(title="Maatla live sales copilot", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("MAATLA_FRONTEND_ORIGIN", "http://localhost:5173")],
                       allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
    db = Store(db_path or os.getenv("MAATLA_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "maatla.db")))
    db.seed()
    hub = Hub()
    buyer_turn_tasks: dict[str, asyncio.Task] = {}
    reply_previews: dict[str, tuple[str, str, str]] = {}
    app.state.store = db
    app.state.hub = hub

    def check(room_id: str, token: str, agent_only: bool = False) -> str:
        role = db.role(room_id, token)
        if role is None or (agent_only and role != "agent"):
            raise HTTPException(403, "Invalid room token")
        return role

    def check_agent_access(key: str):
        configured = os.getenv("MAATLA_AGENT_ACCESS_KEY", "")
        if not configured or not compare_digest(key, configured):
            raise HTTPException(403, "Agent access key required")

    async def process_utterance(room_id: str, role: str, text: str, language: str | None, origin: str):
        room = db.room(room_id)
        if not room or room["ended_at"]:
            return None
        utterance = db.add_utterance(room_id, role, text, language, origin)
        if role == "buyer":
            await hub.both(room_id, {"type": "utterance", "utterance": utterance})
        else:
            await hub.send(room_id, "agent", {"type": "utterance", "utterance": utterance})
        if role != "buyer":
            asyncio.create_task(translate_seller(room_id, utterance))
            return utterance
        headline, why = quick_tip(text)
        quick = db.add_suggestion(room_id, utterance["id"], headline, why, "", [], "instant_rule")
        await hub.send(room_id, "agent", {"type": "suggestion", "suggestion": quick})
        await hub.send(room_id, "agent", {"type": "coach_preview", "utterance_id": utterance["id"],
                                           "text": telugu_live_gist(text)})
        previous = buyer_turn_tasks.get(room_id)
        if previous and not previous.done():
            previous.cancel()
        buyer_turn_tasks[room_id] = asyncio.create_task(finish_buyer_turn(room_id))
        return utterance

    async def finish_buyer_turn(room_id: str):
        # Prisma may split a natural turn after a greeting or pause. Text arrives
        # with each segment; this wait only groups the complete turn for refinement.
        await asyncio.sleep(float(os.getenv("MAATLA_TURN_QUIET_SECONDS", "9")))
        rows = db.rows("SELECT * FROM utterances WHERE room_id=? ORDER BY created_at DESC LIMIT 12", (room_id,))
        if not rows or rows[0]["role"] != "buyer":
            return
        latest = rows[0]
        latest_at = datetime.fromisoformat(latest["created_at"]).timestamp()
        segments = []
        for row in rows:
            if row["role"] != "buyer" or latest_at - datetime.fromisoformat(row["created_at"]).timestamp() > 8:
                break
            segments.append(row["text"])
        combined = " ".join(reversed(segments))
        room = db.room(room_id)
        if not room or room["ended_at"]:
            return
        await hub.send(room_id, "agent", {"type": "coach", "utterance_id": latest["id"],
                                           "text": telugu_coach(combined, db.brief(room["client_id"]))})
        await refine_utterance(room_id, latest, combined)

    async def refine_utterance(room_id: str, utterance: dict, turn_text: str):
        room = db.room(room_id)
        if not room:
            return
        brief = db.brief(room["client_id"])
        knowledge = [x for x in db.rows("SELECT * FROM knowledge WHERE approved=1 ORDER BY updated_at DESC")
                     if is_current(x)]
        try:
            data = await reason(turn_text, "buyer", brief, knowledge)
            if data["meaning_english"]:
                db.set_meaning(utterance["id"], data["meaning_english"])
                await hub.send(room_id, "agent", {"type": "translation", "utterance_id": utterance["id"],
                                                  "meaning": data["meaning_english"]})
            evidence = [{"kind": "client_statement", "utterance_id": utterance["id"], "text": utterance["text"]}]
            evidence += [{"kind": "dated_client_memory", "title": x["field"] + ": " + x["value"],
                          "updated_at": x["observed_at"], "id": x["id"]} for x in brief["current"][:3]]
            matches = find_local(data["meaning_english"] or utterance["text"], knowledge)
            evidence += [{"kind": x["source_kind"], "title": x["title"], "url": x["url"],
                          "updated_at": x["updated_at"], "id": x["id"]} for x in matches]
            suggestion = db.add_suggestion(room_id, utterance["id"], data["next_move"], data["why"],
                                           data["reply_hindi"], evidence, os.getenv("MAATLA_LLM_PROVIDER", "ollama"))
            await hub.send(room_id, "agent", {"type": "suggestion", "suggestion": suggestion})
            allowed_fields = {"budget", "area", "plot_size", "intended_use", "concern", "site_visit", "decision_maker"}
            for item in data["facts"][:8]:
                if not isinstance(item, dict) or item.get("field") not in allowed_fields:
                    continue
                value = str(item.get("value") or "").strip()[:200]
                if value:
                    pending = db.add_pending_fact(room["client_id"], room_id, item["field"], value, utterance["id"])
                    await hub.send(room_id, "agent", {"type": "pending_fact", "fact": pending})
            if data["needs_source"]:
                await source_check(room_id, data["meaning_english"] or turn_text)
        except Exception as exc:
            await hub.send(room_id, "agent", {"type": "provider_error", "provider": "reasoning",
                                              "message": str(exc)[:250]})

    async def translate_seller(room_id: str, utterance: dict):
        try:
            meaning = await translate_seller_to_hindi(utterance["text"])
            if meaning:
                db.set_meaning(utterance["id"], meaning)
                await hub.both(room_id, {"type": "translation", "utterance_id": utterance["id"],
                                         "meaning": meaning})
        except Exception as exc:
            await hub.send(room_id, "agent", {"type": "provider_error", "provider": "seller_translation",
                                              "message": str(exc)[:250]})

    async def source_check(room_id: str, query: str):
        local = find_local(query, db.rows("SELECT * FROM knowledge WHERE approved=1"))
        results = [{"title": x["title"], "url": x["url"], "kind": x["source_kind"],
                    "updated_at": x["updated_at"], "excerpt": x["body"][:500]} for x in local]
        await hub.send(room_id, "agent", {"type": "sources", "query": query,
                                          "results": results, "checked_at": now()})
        try:
            web = await search_authorized(query)
            results += [{"title": x["title"], "url": x["url"], "kind": "allowlisted_web_search",
                         "updated_at": now(), "excerpt": x["snippet"]} for x in web]
        except Exception as exc:
            await hub.send(room_id, "agent", {"type": "provider_error", "provider": "web_search",
                                              "message": str(exc)[:250]})
        await hub.send(room_id, "agent", {"type": "sources", "query": query, "results": results,
                                          "checked_at": now()})
        return results

    @app.get("/api/health")
    def health():
        return {"status": "ok", "gnani_configured": bool(os.getenv("GNANI_API_KEY")),
                "agent_access_required": True,
                "llm_provider": os.getenv("MAATLA_LLM_PROVIDER", "ollama"),
                "demo_mode": os.getenv("MAATLA_DEMO_MODE", "true").lower() == "true",
                "web_search_configured": bool(os.getenv("BRAVE_SEARCH_API_KEY"))}

    @app.get("/api/clients")
    def clients(x_agent_key: str = Header(default="")):
        check_agent_access(x_agent_key)
        return db.rows("SELECT * FROM clients ORDER BY created_at DESC")

    @app.post("/api/clients")
    def create_client(body: ClientCreate, x_agent_key: str = Header(default="")):
        check_agent_access(x_agent_key)
        return db.create_client(body.name, body.phone)

    @app.get("/api/clients/{client_id}/brief")
    def brief(client_id: str, x_agent_key: str = Header(default="")):
        check_agent_access(x_agent_key)
        if not db.rows("SELECT id FROM clients WHERE id=?", (client_id,)):
            raise HTTPException(404, "Client not found")
        return db.brief(client_id)

    @app.post("/api/rooms")
    def create_room(body: RoomCreate, x_agent_key: str = Header(default="")):
        check_agent_access(x_agent_key)
        if not db.rows("SELECT id FROM clients WHERE id=?", (body.client_id,)):
            raise HTTPException(404, "Client not found")
        return db.create_room(body.client_id)

    @app.get("/api/rooms/{room_id}")
    def room_state(room_id: str, x_room_token: str = Header(default="")):
        role = check(room_id, x_room_token)
        return db.state(room_id, role)

    @app.get("/api/rooms/{room_id}/rtc-config")
    def rtc_config(room_id: str, x_room_token: str = Header(default="")):
        check(room_id, x_room_token)
        servers = [{"urls": ["stun:stun.l.google.com:19302"]}]
        turn_url = os.getenv("MAATLA_TURN_URL", "")
        if turn_url:
            servers.append({"urls": [turn_url], "username": os.getenv("MAATLA_TURN_USERNAME", ""),
                            "credential": os.getenv("MAATLA_TURN_CREDENTIAL", "")})
        return {"iceServers": servers}

    @app.post("/api/rooms/{room_id}/demo-turn")
    async def demo_turn(room_id: str, body: DemoTurn, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        if db.room(room_id)["ended_at"]:
            raise HTTPException(409, "Call has ended")
        if os.getenv("MAATLA_DEMO_MODE", "true").lower() != "true":
            raise HTTPException(403, "Demo input is disabled")
        return await process_utterance(room_id, body.role, body.text, body.language, "synthetic_demo")

    @app.post("/api/rooms/{room_id}/facts/approve")
    def approve_facts(room_id: str, body: FactApproval, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        return db.approve_pending(room_id, body.ids)

    @app.post("/api/rooms/{room_id}/facts/reject")
    def reject_facts(room_id: str, body: FactApproval, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        db.reject_pending(room_id, body.ids)
        return {"rejected": body.ids}

    @app.post("/api/rooms/{room_id}/facts/manual")
    def manual_fact(room_id: str, body: ManualFact, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        source = db.rows("SELECT id,created_at FROM utterances WHERE id=? AND room_id=? AND role='buyer'",
                         (body.source_utterance_id, room_id))
        if not source:
            raise HTTPException(400, "Choose a buyer statement from this call")
        room = db.room(room_id)
        return db.add_fact(room["client_id"], body.field, body.value.strip(),
                           "agent_reviewed_client_statement", body.source_utterance_id, source[0]["created_at"])

    @app.post("/api/rooms/{room_id}/commitments")
    def create_commitment(room_id: str, body: CommitmentCreate, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        room = db.room(room_id)
        assert room is not None
        if body.source_utterance_id and not db.rows("SELECT id FROM utterances WHERE id=? AND room_id=?", (body.source_utterance_id, room_id)):
            raise HTTPException(400, "Source utterance is not in this call")
        return db.add_commitment(room["client_id"], room_id, body.text, body.owner,
                                 body.source_utterance_id, body.due_at)

    @app.post("/api/rooms/{room_id}/commitments/{commitment_id}/complete")
    def complete_commitment(room_id: str, commitment_id: str, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        room = db.room(room_id)
        rows = db.rows("SELECT id FROM commitments WHERE id=? AND client_id=? AND status='open'",
                       (commitment_id, room["client_id"]))
        if not rows:
            raise HTTPException(404, "Open commitment not found")
        db.execute("UPDATE commitments SET status='complete' WHERE id=?", (commitment_id,))
        return {"completed": commitment_id}

    @app.get("/api/rooms/{room_id}/summary")
    def summary(room_id: str, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        return db.summary(room_id)

    @app.post("/api/rooms/{room_id}/knowledge")
    def add_knowledge(room_id: str, body: KnowledgeCreate, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        if body.url and not body.url.startswith("https://"):
            raise HTTPException(400, "Knowledge URL must be HTTPS")
        kid = "kb-" + uuid4().hex[:12]
        db.execute("INSERT INTO knowledge VALUES (?,?,?,?,?,?,?,?)",
                   (kid, body.title, body.body, body.url, body.source_kind, 1, now(), body.expires_at))
        return db.rows("SELECT * FROM knowledge WHERE id=?", (kid,))[0]

    @app.post("/api/rooms/{room_id}/sources")
    async def sources(room_id: str, body: SearchRequest, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        fetch_task = asyncio.create_task(fetch_authorized(body.fetch_url)) if body.fetch_url else None
        results = await source_check(room_id, body.query)
        if body.fetch_url:
            try:
                assert fetch_task is not None
                page = await fetch_task
                results.append({"title": page["title"], "url": page["url"], "kind": "allowlisted_fetched_page",
                                "updated_at": now(), "excerpt": page["text"][:700]})
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(502, "Approved source could not be fetched") from exc
        return {"results": results, "checked_at": now()}

    @app.post("/api/rooms/{room_id}/reply-preview")
    async def reply_preview(room_id: str, body: ReplyPreviewRequest, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        if db.room(room_id)["ended_at"]:
            raise HTTPException(409, "Call has ended")
        exact_text = body.text.strip()
        if not exact_text:
            raise HTTPException(400, "Reply cannot be empty")
        try:
            telugu = await translate_reply_to_telugu(exact_text)
        except Exception as exc:
            raise HTTPException(503, "Telugu preview unavailable: " + str(exc)[:180]) from exc
        for old_id, old_preview in list(reply_previews.items()):
            if old_preview[0] == room_id:
                reply_previews.pop(old_id, None)
        preview_id = str(uuid4())
        reply_previews[preview_id] = (room_id, exact_text, telugu)
        return {"preview_id": preview_id, "hinglish": exact_text, "telugu": telugu}

    @app.post("/api/rooms/{room_id}/speak")
    async def speak(room_id: str, body: SpeakRequest, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        if db.room(room_id)["ended_at"]:
            raise HTTPException(409, "Call has ended")
        if "buyer" not in hub.rooms.get(room_id, {}):
            raise HTTPException(409, "Buyer must join before an audio reply can be sent")
        rows = db.rows("SELECT reply_hindi FROM suggestions WHERE id=? AND room_id=?", (body.suggestion_id, room_id))
        if not rows or not rows[0]["reply_hindi"]:
            raise HTTPException(404, "No approved reply available")
        approved_text = (body.approved_text or rows[0]["reply_hindi"]).strip()
        if not approved_text:
            raise HTTPException(400, "Approved reply cannot be empty")
        preview = reply_previews.get(body.preview_id)
        if not preview or preview[0] != room_id or preview[1] != approved_text:
            raise HTTPException(409, "Review the current Telugu meaning before speaking")
        try:
            audio = await synthesize_hinglish(approved_text)
        except Exception as exc:
            raise HTTPException(503, "Gnani TTS unavailable: " + str(exc)[:180]) from exc
        event = {"type": "approved_reply", "text": approved_text, "language": "hi-en",
                 "audio_base64": base64.b64encode(audio).decode("ascii")}
        if "buyer" not in hub.rooms.get(room_id, {}):
            raise HTTPException(409, "Buyer left before the audio reply was ready")
        reply_previews.pop(body.preview_id, None)
        spoken = db.add_utterance(room_id, "agent", approved_text, "hi-en", "approved_tts")
        await hub.both(room_id, {"type": "utterance", "utterance": spoken})
        await hub.both(room_id, event)
        return {"sent": True, "text": approved_text, "reviewed_telugu": preview[2],
                "utterance_id": spoken["id"]}

    @app.post("/api/rooms/{room_id}/coach-voice")
    async def coach_voice(room_id: str, body: CoachVoiceRequest, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        utterances = db.rows("SELECT text,created_at FROM utterances WHERE id=? AND room_id=? AND role='buyer'",
                             (body.utterance_id, room_id))
        if not utterances:
            raise HTTPException(404, "Buyer turn not found")
        room = db.room(room_id)
        segments = db.rows("SELECT text,created_at,role FROM utterances WHERE room_id=? AND created_at<=? ORDER BY created_at DESC LIMIT 12",
                           (room_id, utterances[0]["created_at"]))
        latest_at = datetime.fromisoformat(utterances[0]["created_at"]).timestamp()
        texts = []
        for segment in segments:
            if segment["role"] != "buyer" or latest_at - datetime.fromisoformat(segment["created_at"]).timestamp() > 8:
                break
            texts.append(segment["text"])
        coach = telugu_coach(" ".join(reversed(texts)), db.brief(room["client_id"]))
        try:
            audio = await synthesize_telugu_coach(coach)
        except Exception as exc:
            raise HTTPException(503, "Gnani private coach voice unavailable: " + str(exc)[:180]) from exc
        return {"text": coach, "language": "te-IN", "audio_base64": base64.b64encode(audio).decode("ascii")}

    @app.post("/api/rooms/{room_id}/end")
    async def end_room(room_id: str, x_room_token: str = Header(default="")):
        check(room_id, x_room_token, True)
        db.execute("UPDATE rooms SET ended_at=? WHERE id=? AND ended_at IS NULL", (now(), room_id))
        await hub.both(room_id, {"type": "call_ended"})
        return db.state(room_id, "agent")

    @app.websocket("/ws/rooms/{room_id}")
    async def room_socket(ws: WebSocket, room_id: str):
        await ws.accept()
        try:
            auth = await asyncio.wait_for(ws.receive_json(), timeout=10)
        except (ValueError, asyncio.TimeoutError, WebSocketDisconnect):
            await ws.close(code=4403)
            return
        token = auth.get("token", "") if isinstance(auth, dict) and auth.get("type") == "auth" else ""
        role = db.role(room_id, token)
        room = db.room(room_id)
        if not role or not room or room["ended_at"]:
            await ws.close(code=4403)
            return
        other_role = "buyer" if role == "agent" else "agent"
        other_present = other_role in hub.rooms.get(room_id, {})
        hub.rooms.setdefault(room_id, {})[role] = ws
        await ws.send_json({"type": "state", "state": db.state(room_id, role)})
        if other_present:
            await ws.send_json({"type": "peer_present", "role": other_role})
        await hub.send(room_id, other_role, {"type": "peer_joined", "role": role})
        stt = GnaniSTT(role, lambda text, lang: process_utterance(room_id, role, text, lang, "gnani_prisma"),
                       lambda message: hub.send(room_id, role, {"type": "provider_error", "provider": "gnani_prisma",
                                                                "message": message}))
        try:
            if os.getenv("GNANI_API_KEY"):
                try:
                    await stt.start()
                    await ws.send_json({"type": "provider_ready", "provider": "gnani_prisma"})
                except Exception as exc:
                    await ws.send_json({"type": "provider_error", "provider": "gnani_prisma", "message": str(exc)[:250]})
            else:
                await ws.send_json({"type": "provider_error", "provider": "gnani_prisma",
                                    "message": "GNANI_API_KEY is not configured; use labelled demo turns only."})
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    try:
                        await stt.send(message["bytes"])
                    except Exception as exc:
                        await ws.send_json({"type": "provider_error", "provider": "gnani_prisma", "message": str(exc)[:250]})
                elif message.get("text"):
                    try:
                        import json
                        event = json.loads(message["text"])
                    except ValueError:
                        continue
                    if event.get("type") in {"offer", "answer", "ice"}:
                        await hub.send(room_id, "buyer" if role == "agent" else "agent", event)
        except WebSocketDisconnect:
            pass
        finally:
            await stt.close()
            if hub.rooms.get(room_id, {}).get(role) is ws:
                hub.rooms[room_id].pop(role, None)
                await hub.send(room_id, "buyer" if role == "agent" else "agent",
                               {"type": "peer_left", "role": role})

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    return app


app = create_app()
