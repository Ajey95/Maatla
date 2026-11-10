from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store
from app.knowledge import find_local
from app.providers import (guard_property_claims, exact_unit_facts, extract_client_facts,
                           anchored_meaning, safe_hinglish_reply, telugu_coach, telugu_live_gist,
                           translate_seller_to_hindi)


def test_temporal_fact_preserves_history_and_current_value(tmp_path):
    db = Store(str(tmp_path / "facts.db"))
    client = db.create_client("Buyer")
    first = db.add_fact(client["id"], "budget", "₹80 lakh", "client_statement", "utt-old")
    second = db.add_fact(client["id"], "budget", "₹95 lakh", "client_statement", "utt-new")
    brief = db.brief(client["id"])
    assert len([x for x in brief["history"] if x["field"] == "budget"]) == 2
    assert brief["current"][0]["id"] == second["id"]
    older = next(x for x in brief["history"] if x["id"] == first["id"])
    assert older["valid_to"] == second["valid_from"]
    late_review = db.add_fact(client["id"], "budget", "₹70 lakh", "agent_reviewed_client_statement",
                              "utt-earlier", "2020-01-01T00:00:00+00:00")
    assert db.brief(client["id"])["current"][0]["id"] == second["id"]
    assert late_review["valid_to"] == first["valid_from"]


def test_expired_seller_note_is_not_used_as_current_evidence():
    entries = [{"approved": 1, "title": "Plot access", "body": "Road access confirmed",
                "expires_at": "2020-01-01T00:00:00Z"}]
    assert find_local("road access", entries) == []


def test_lakh_and_plot_size_preserved_and_unsupported_title_claim_blocked():
    assert exact_unit_facts("मेरा बजट 80 लाख है; 200 गज का प्लॉट चाहिए") == [
        {"field": "budget", "value": "80 लाख"}, {"field": "plot_size", "value": "200 गज"}]
    unsafe = {"next_move": "Say papers are clear", "why": "Model guess",
              "reply_hindi": "रजिस्ट्री के कागज़ पक्के हैं और सड़क 200 गज दूर है।", "needs_source": False}
    safe = guard_property_claims("रजिस्ट्री के कागज़ पक्के हैं क्या? सड़क का रास्ता?", unsafe)
    assert safe["needs_source"] is True
    assert "पक्के हैं" not in safe["reply_hindi"]
    assert "200" not in safe["reply_hindi"]
    stray = extract_client_facts("अगर documents ठीक हैं तो शनिवार को site visit कर सकते हैं।",
                                 "If documents are correct, we can visit Saturday.",
                                 [{"field": "area", "value": "not specified"},
                                  {"field": "budget", "value": "₹80 lakh"},
                                  {"field": "concern", "value": "road access"}])
    assert not any(x["field"] in {"area", "budget"} for x in stray)
    assert not any("road" in x["value"] for x in stray)
    assert any(x["field"] == "site_visit" and "conditional" in x["value"] for x in stray)


def test_spoken_units_and_short_telugu_sales_phrases_are_safe():
    buyer = "मेरा बजट अस्सी लाख है, दो सौ गज का प्लॉट चाहिए, Saturday को साइट विज़िट कर सकते हैं क्या?"
    assert exact_unit_facts(buyer) == [
        {"field": "budget", "value": "80 लाख"}, {"field": "plot_size", "value": "200 गज"}]
    meaning = anchored_meaning(buyer, "Wrong model guess")
    assert "80 lakh" in meaning and "200 square-yard" in meaning and "Saturday" in meaning
    assert "Wrong model guess" not in meaning
    assert "availability check" in safe_hinglish_reply(buyer)
    assert "బడ్జెట్ 80 లక్షలు" in telugu_coach(buyer)
    assert "రిజిస్ట్రీ పత్రాలు" in telugu_coach("registry papers और road access verify करिए")
    assert "బడ్జెట్ 80 లక్షలు" in telugu_live_gist("मेरा budget 80 लाख है")
    assert "రిజిస్ట్రీ పత్రాలు" in telugu_live_gist("registry papers और road access")


def test_telugu_domain_phrase_caption_is_literal():
    import asyncio
    assert asyncio.run(translate_seller_to_hindi("నమస్తే")) == "नमस्ते।"
    translated = asyncio.run(translate_seller_to_hindi(
        "డాక్యుమెంట్స్ మరియు రోడ్ యాక్సెస్ చెక్ చేసి మీకు చెప్తాను."))
    assert "verify" in translated and "बताऊँगा" in translated


def test_buyer_token_cannot_read_agent_memory_or_write_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("MAATLA_DEMO_MODE", "true")
    monkeypatch.setenv("MAATLA_AGENT_ACCESS_KEY", "test-agent-private-key")
    app = create_app(str(tmp_path / "api.db"))
    with TestClient(app) as http:
        assert http.get("/api/clients").status_code == 403
        room = http.post("/api/rooms", headers={"X-Agent-Key": "test-agent-private-key"},
                         json={"client_id": "sample-client"}).json()
        buyer = http.get("/api/rooms/" + room["id"], headers={"X-Room-Token": room["buyer_token"]})
        assert buyer.status_code == 200
        assert "brief" not in buyer.json()
        blocked = http.post("/api/rooms/" + room["id"] + "/demo-turn",
                            headers={"X-Room-Token": room["buyer_token"]},
                            json={"role": "buyer", "text": "price?"})
        assert blocked.status_code == 403
        private_seller_turn = http.post("/api/rooms/" + room["id"] + "/demo-turn",
                                        headers={"X-Room-Token": room["agent_token"]},
                                        json={"role": "agent", "text": "seller's private Telugu note"})
        assert private_seller_turn.status_code == 200
        buyer_state = http.get("/api/rooms/" + room["id"],
                               headers={"X-Room-Token": room["buyer_token"]}).json()
        assert not any(u["role"] == "agent" for u in buyer_state["utterances"])
        assert http.post("/api/rooms/" + room["id"] + "/coach-voice",
                         headers={"X-Room-Token": room["buyer_token"]},
                         json={"utterance_id": "missing"}).status_code == 403
        assert http.post("/api/rooms/" + room["id"] + "/reply-preview",
                         headers={"X-Room-Token": room["buyer_token"]},
                         json={"text": "नमस्ते"}).status_code == 403


def test_edited_hinglish_requires_matching_telugu_preview(tmp_path, monkeypatch):
    import app.main as main

    monkeypatch.setenv("MAATLA_AGENT_ACCESS_KEY", "test-agent-private-key")
    monkeypatch.setenv("MAATLA_PREWARM_LLM", "false")

    async def fake_translate(text):
        return "తెలుగు అర్థం: " + text

    async def fake_tts(_text):
        return b"RIFF-demo"

    monkeypatch.setattr(main, "translate_reply_to_telugu", fake_translate)
    monkeypatch.setattr(main, "synthesize_hinglish", fake_tts)
    app = create_app(str(tmp_path / "preview.db"))

    class FakeSocket:
        def __init__(self):
            self.messages = []

        async def send_json(self, message):
            self.messages.append(message)

    with TestClient(app) as http:
        room = http.post("/api/rooms", headers={"X-Agent-Key": "test-agent-private-key"},
                         json={"client_id": "sample-client"}).json()
        room_id = room["id"]
        headers = {"X-Room-Token": room["agent_token"]}
        utterance = app.state.store.add_utterance(room_id, "buyer", "मुझे papers चाहिए", "hi-IN", "test")
        suggestion = app.state.store.add_suggestion(room_id, utterance["id"], "Check papers", "Buyer asked",
                                                    "मैं papers भेजूँगा", [], "test")
        agent, buyer = FakeSocket(), FakeSocket()
        app.state.hub.rooms[room_id] = {"agent": agent, "buyer": buyer}
        first = http.post(f"/api/rooms/{room_id}/reply-preview", headers=headers,
                          json={"text": "मैं papers भेजूँगा"}).json()
        edited = "मैं verified papers पहले भेजूँगा"
        second = http.post(f"/api/rooms/{room_id}/reply-preview", headers=headers,
                           json={"text": edited}).json()
        assert second["telugu"].endswith(edited)
        stale = http.post(f"/api/rooms/{room_id}/speak", headers=headers,
                          json={"suggestion_id": suggestion["id"], "approved_text": edited,
                                "preview_id": first["preview_id"]})
        assert stale.status_code == 409
        sent = http.post(f"/api/rooms/{room_id}/speak", headers=headers,
                         json={"suggestion_id": suggestion["id"], "approved_text": edited,
                               "preview_id": second["preview_id"]})
        assert sent.status_code == 200
        assert buyer.messages[-1]["text"] == edited
        buyer_state = http.get(f"/api/rooms/{room_id}",
                               headers={"X-Room-Token": room["buyer_token"]}).json()
        assert any(u["text"] == edited and u["origin"] == "approved_tts"
                   for u in buyer_state["utterances"])


def test_synthetic_turn_is_labelled_and_summary_uses_only_reviewed_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("MAATLA_DEMO_MODE", "true")
    monkeypatch.setenv("MAATLA_AGENT_ACCESS_KEY", "test-agent-private-key")
    app = create_app(str(tmp_path / "journey.db"))
    with TestClient(app) as http:
        room = http.post("/api/rooms", headers={"X-Agent-Key": "test-agent-private-key"},
                         json={"client_id": "sample-client"}).json()
        headers = {"X-Room-Token": room["agent_token"]}
        class Capture:
            messages = []

            async def send_json(self, message):
                self.messages.append(message)

        app.state.hub.rooms[room["id"]] = {"agent": Capture()}
        result = http.post("/api/rooms/" + room["id"] + "/demo-turn", headers=headers,
                           json={"role": "buyer", "text": "रजिस्ट्री के दस्तावेज़ दिखाइए।"})
        assert result.status_code == 200
        assert result.json()["origin"] == "synthetic_demo"
        assert any(m["type"] == "coach_preview" and "రిజిస్ట్రీ" in m["text"]
                   for m in app.state.hub.rooms[room["id"]]["agent"].messages)
        state = http.get("/api/rooms/" + room["id"], headers=headers).json()
        assert state["suggestions"][0]["provider"] == "instant_rule"
        pending = app.state.store.add_pending_fact("sample-client", room["id"], "budget", "₹95 lakh", result.json()["id"])
        summary = http.get("/api/rooms/" + room["id"] + "/summary", headers=headers).json()
        assert any(x["id"] == pending["id"] for x in summary["pending_review"])
        assert not any(x["value"] == "₹95 lakh" for x in summary["buyer_requirements"])
        approved = http.post("/api/rooms/" + room["id"] + "/facts/approve",
                             headers=headers, json={"ids": [pending["id"]]})
        assert approved.status_code == 200
        summary = http.get("/api/rooms/" + room["id"] + "/summary", headers=headers).json()
        assert any(x["value"] == "₹95 lakh" for x in summary["buyer_requirements"])
        manual = http.post("/api/rooms/" + room["id"] + "/facts/manual", headers=headers,
                           json={"field": "area", "value": "Near Hyderabad", "source_utterance_id": result.json()["id"]})
        assert manual.status_code == 200
        assert manual.json()["source_id"] == result.json()["id"]


def test_two_person_signaling_and_agent_only_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MAATLA_AGENT_ACCESS_KEY", "test-agent-private-key")
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    app = create_app(str(tmp_path / "signaling.db"))
    with TestClient(app) as http:
        room = http.post("/api/rooms", headers={"X-Agent-Key": "test-agent-private-key"},
                         json={"client_id": "sample-client"}).json()
        path = "/ws/rooms/" + room["id"]
        with http.websocket_connect(path) as agent:
            agent.send_json({"type": "auth", "token": room["agent_token"]})
            assert agent.receive_json()["type"] == "state"
            assert agent.receive_json()["type"] == "provider_error"
            with http.websocket_connect(path) as buyer:
                buyer.send_json({"type": "auth", "token": room["buyer_token"]})
                buyer_state = buyer.receive_json()
                assert "brief" not in buyer_state["state"]
                assert buyer.receive_json()["type"] == "peer_present"
                assert agent.receive_json()["type"] == "peer_joined"
                assert buyer.receive_json()["type"] == "provider_error"
                agent.send_json({"type": "offer", "sdp": {"type": "offer", "sdp": "test-sdp"}})
                assert buyer.receive_json()["type"] == "offer"
                buyer.send_json({"type": "answer", "sdp": {"type": "answer", "sdp": "test-sdp"}})
                assert agent.receive_json()["type"] == "answer"
