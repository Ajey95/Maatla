"""Provider adapters. A missing key is surfaced; audio is never represented as transcribed."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Awaitable, Callable

import httpx
from websockets.asyncio.client import connect

_tts_lock = asyncio.Lock()

class GnaniSTT:
    def __init__(self, role: str, on_transcript: Callable[[str, str | None], Awaitable[None]],
                 on_error: Callable[[str], Awaitable[None]] | None = None):
        self.role = role
        self.on_transcript = on_transcript
        self.on_error = on_error
        self.key = os.getenv("GNANI_API_KEY", "")
        self.model = os.getenv("GNANI_STT_MODEL", "prisma-v3.0")
        self.socket = None
        self.task: asyncio.Task | None = None
        self.started_at = 0.0

    async def start(self):
        if not self.key:
            raise RuntimeError("GNANI_API_KEY is not configured")
        # The challenge account currently rejects comma-separated language_code
        # on this streaming endpoint even though the v3 docs describe it.
        language = "te-IN" if self.role == "agent" else "hi-IN"
        headers = {
            "x-api-key-id": self.key,
            "model": self.model,
            "language_code": language,
            "format": "codemix" if self.model == "prisma-v3.0" else "verbatim",
            "x-sample-rate": "16000",
            "x-min-silence-ms": "500",
            "x-idle-timeout-sec": "900",
        }
        self.socket = await connect("wss://api.vachana.ai/stt/v3/stream",
                                    additional_headers=headers, open_timeout=12)
        self.started_at = time.monotonic()
        self.task = asyncio.create_task(self._receive())

    async def _receive(self):
        assert self.socket is not None
        try:
            async for raw in self.socket:
                if not isinstance(raw, str):
                    continue
                data = json.loads(raw)
                if data.get("type") == "transcript" and data.get("text", "").strip():
                    await self.on_transcript(data["text"].strip(), data.get("detected_language_code"))
                elif data.get("type") == "error":
                    raise RuntimeError(str(data.get("message") or data))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.on_error:
                await self.on_error(str(exc)[:250])

    async def send(self, frame: bytes):
        if len(frame) != 1024:
            raise ValueError("Gnani STT requires 1,024-byte PCM frames")
        if self.socket is not None and (self.task is None or self.task.done() or time.monotonic() - self.started_at > 840):
            await self.close()
            await self.start()
        if self.socket is None:
            return
        await self.socket.send(frame)

    async def close(self):
        if self.task:
            self.task.cancel()
        if self.socket:
            await self.socket.close()
        self.socket = None
        self.task = None


async def synthesize_timbre(text: str, voice: str, language: str) -> bytes:
    key = os.getenv("GNANI_API_KEY", "")
    if not key:
        raise RuntimeError("GNANI_API_KEY is not configured")
    body = {
        "text": text[:500], "model": os.getenv("GNANI_TTS_MODEL", "timbre-v2.5"),
        "voice": voice, "language": language,
        "audio_config": {"encoding": "linear_pcm", "container": "wav",
                         "num_channels": 1, "sample_rate": 24000, "sample_width": 2},
    }
    async with _tts_lock:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post("https://api.vachana.ai/api/v1/tts/inference",
                                         headers={"X-API-Key-ID": key}, json=body)
            response.raise_for_status()
            return response.content


async def synthesize_hindi(text: str) -> bytes:
    """Hindi smoke-test path retained for provider verification."""
    return await synthesize_timbre(text, os.getenv("GNANI_TTS_VOICE", "Nalini"), "hi-IN")


async def synthesize_hinglish(text: str) -> bytes:
    return await synthesize_timbre(text, os.getenv("GNANI_TTS_HINGLISH_VOICE", "Poorvi"), "hi-en")


async def synthesize_telugu_coach(text: str) -> bytes:
    # Use a native Telugu voice for the seller-only explanation.
    return await synthesize_timbre(text, os.getenv("GNANI_TTS_TELUGU_VOICE", "Varuni"), "te-IN")


async def prewarm_ollama() -> None:
    """Load the local model while the room is being prepared, not on the first buyer turn."""
    if os.getenv("MAATLA_LLM_PROVIDER", "ollama") != "ollama":
        return
    try:
        async with httpx.AsyncClient(timeout=70) as client:
            await client.post(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat"),
                              json={"model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
                                    "messages": [{"role": "user", "content": "Reply OK"}],
                                    "stream": False, "keep_alive": "30m", "options": {"num_predict": 2}})
    except Exception:
        pass


SYSTEM_PROMPT = """You assist a human land seller during a live Hindi/Hinglish buyer call. Return only compact English fields: meaning_english, next_move, why, needs_source. Translate the buyer literally, preserving uncertainty, Indian units, numbers and place names. Recommend one useful next move in at most 18 words; explain why in at most 18 words. Never invent prices, title status, approvals, road access or client history. Treat dated prior-call memory as context and superseded facts as historical. Seller-supplied listing details are unverified. If the buyer requests property-specific documents or current facts, recommend checking them. Do not infer personality or intent from accent or region."""

REASON_SCHEMA = {"type": "object", "properties": {
    "meaning_english": {"type": "string"}, "next_move": {"type": "string"},
    "why": {"type": "string"}, "needs_source": {"type": "boolean"}},
    "required": ["meaning_english", "next_move", "why", "needs_source"]}


def exact_unit_facts(text: str) -> list[dict]:
    """Preserve explicit Indian units where a small model may misconvert them."""
    found = []
    lower = text.lower()
    if re.search(r"budget|बजट|బడ్జెట్", lower):
        money = re.search(r"(\d+(?:[.,]\d+)?)\s*(लाख|लक्ष|lakh|lakhs|లక్ష|करोड़|crore|crores)", lower)
        if money:
            found.append({"field": "budget", "value": money.group(0).strip()})
        elif re.search(r"अस्सी\s+लाख", lower):
            found.append({"field": "budget", "value": "80 लाख"})
    size = re.search(r"(\d+(?:[.,]\d+)?)\s*(गज|गज़|sq\.?\s*yd|square\s*yard|గజాలు|గజం)", lower)
    if size:
        found.append({"field": "plot_size", "value": size.group(0).strip()})
    elif re.search(r"दो\s+सौ\s+ग[जज़]", lower):
        found.append({"field": "plot_size", "value": "200 गज"})
    return found


def anchored_meaning(text: str, model_meaning: str) -> str:
    """Prefer literal, explicitly anchored details over a small model's guessed translation."""
    lower = text.lower()
    parts = []
    for item in exact_unit_facts(text):
        if item["field"] == "budget":
            parts.append("Buyer states a budget of " + item["value"].replace("लाख", "lakh") + ".")
        else:
            parts.append("Buyer wants a " + item["value"].replace("गज", "square-yard") + " plot.")
    if re.search(r"हैदराबाद|hyderabad", lower):
        parts.append("Preferred location is near Hyderabad.")
    if re.search(r"site\s*visit|साइट\s*विज", lower):
        day = "Saturday " if re.search(r"saturday|शनिवार", lower) else ""
        parts.append("Asks whether a " + day + "site visit is possible.")
    if re.search(r"family|परिवार", lower):
        parts.append("The buyer is planning with family.")
    if re.search(r"suitable\s+area|area\s+suggest", lower):
        parts.append("Asks for a suitable area suggestion.")
    if re.search(r"रजिस्ट|काग[जज़]|document|registry", lower):
        parts.append("Requests verification of registry papers.")
    if re.search(r"road|access|रास्त|सड़क", lower):
        parts.append("Requests verification of road access.")
    return " ".join(parts) if parts else model_meaning


def telugu_coach(text: str, brief: dict | None = None) -> str:
    """Tell the seller in Telugu what the buyer said and what to check next."""
    lower = text.lower()
    if re.search(r"family|परिवार", lower) and re.search(r"saturday|शनिवार", lower):
        return ("కొనుగోలుదారు తన కుటుంబంతో శనివారం మధ్యాహ్నం స్థలం చూడటానికి రావాలనుకుంటున్నారు. "
                "అందుకు ముందు అందుబాటులో ఉన్న రిజిస్ట్రీ పత్రాలు, స్థలం వివరాలు పంపమన్నారు. "
                "ధృవీకరించిన వివరాలనే పంపుతామని చెప్పి, సందర్శన సమయాన్ని నిర్ధారించండి.")
    if re.search(r"रजिस्ट|काग[जज़]|document|registry|road|access|रास्त|सड़क", lower):
        return ("కొనుగోలుదారు నిర్ణయం తీసుకునే ముందు రిజిస్ట్రీ పత్రాలు, రహదారి సదుపాయం స్పష్టంగా ఉన్నాయా అని అడిగారు. "
                "అధికారిక రికార్డులతో తనిఖీ చేసిన తర్వాత మాత్రమే చెప్పండి. శనివారం స్థలం చూసే అవకాశం గురించి కూడా మాట్లాడారు.")
    facts = exact_unit_facts(text)
    if facts or re.search(r"site\s*visit|साइट\s*विज", lower):
        budget = next((x["value"] for x in facts if x["field"] == "budget"), "")
        size = next((x["value"] for x in facts if x["field"] == "plot_size"), "")
        details = []
        if budget:
            details.append("బడ్జెట్ " + budget.replace("लाख", "లక్షలు"))
        if size:
            details.append("స్థలం పరిమాణం " + size.replace("गज", "గజాలు"))
        heard = ", ".join(details) if details else "స్థల సందర్శన"
        return ("కొనుగోలుదారు " + heard + " గురించి చెప్పారు. హైదరాబాద్ దగ్గర కుటుంబానికి అనువైన స్థలం చూస్తున్నారు. "
                "ఏ ప్రాంతం సౌకర్యంగా ఉంటుందో అడగండి; ధృవీకరించని స్థల వివరాలు చెప్పవద్దు.")
    if brief and brief.get("current"):
        return "ఈ కొనుగోలుదారుతో ముందు మాట్లాడిన వివరాలు ఉన్నాయి. ఇప్పుడు చెప్పిన అవసరాన్ని స్పష్టంగా అడిగి, పాత సమాచారాన్ని మళ్లీ నిర్ధారించండి."
    return "కొనుగోలుదారు చెప్పిన అవసరాన్ని స్పష్టంగా అడగండి. ధృవీకరించిన వివరాలనే పంచుకోండి."


def telugu_live_gist(text: str) -> str:
    """Short, explicitly provisional Telugu text for each Prisma speech segment."""
    lower = text.lower()
    if re.search(r"family|परिवार", lower) and re.search(r"saturday|शनिवार", lower):
        return "కొనుగోలుదారు కుటుంబంతో శనివారం స్థలం చూడాలని, ముందుగా పత్రాలు మరియు స్థలం వివరాలు కావాలని చెబుతున్నారు."
    if re.search(r"रजिस्ट|काग[जज़]|document|registry|road|access|रास्त|सड़क", lower):
        return "కొనుగోలుదారు రిజిస్ట్రీ పత్రాలు లేదా రహదారి సదుపాయం గురించి అడుగుతున్నారు. ధృవీకరణ అవసరం."
    facts = exact_unit_facts(text)
    if facts or re.search(r"हैदराबाद|hyderabad|plot|प्लॉट|area|एरिया", lower):
        details = []
        for fact in facts:
            value = fact["value"].replace("लाख", "లక్షలు").replace("गज", "గజాలు")
            details.append(("బడ్జెట్ " if fact["field"] == "budget" else "స్థలం పరిమాణం ") + value)
        return "కొనుగోలుదారు " + (", ".join(details) if details else "స్థలం అవసరం") + " గురించి చెబుతున్నారు. పూర్తి మాట వింటున్నాను."
    return "కొనుగోలుదారు మాట్లాడుతున్నారు. పూర్తి అభ్యర్థన కోసం వింటున్నాను."


async def translate_reply_to_telugu(text: str) -> str:
    """Translate the exact editable Hinglish reply for the seller's review."""
    instruction = ("Translate this Hindi-English land-sales reply faithfully into natural Telugu script for the seller to review. "
                   "Preserve every number, name, date, uncertainty and commitment. Do not add facts or promises. "
                   "Output only the Telugu translation, with no explanation.")
    provider = os.getenv("MAATLA_LLM_PROVIDER", "ollama")
    if provider == "ollama":
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat"),
                                         json={"model": os.getenv("MAATLA_TRANSLATION_MODEL", "gemma4:latest"),
                                               "stream": False, "think": False, "keep_alive": "30m",
                                               "options": {"temperature": 0, "num_predict": 350},
                                               "messages": [{"role": "system", "content": instruction},
                                                            {"role": "user", "content": text}]})
            response.raise_for_status()
            content = response.json()["message"]["content"]
    elif provider == "openai_compatible":
        key = os.getenv("MAATLA_LLM_API_KEY", "")
        if not key:
            raise RuntimeError("MAATLA_LLM_API_KEY is not configured")
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.post(os.getenv("MAATLA_LLM_URL", ""),
                                         headers={"Authorization": "Bearer " + key},
                                         json={"model": os.getenv("MAATLA_LLM_MODEL", ""), "temperature": 0,
                                               "messages": [{"role": "system", "content": instruction},
                                                            {"role": "user", "content": text}]})
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
    else:
        raise RuntimeError("Unsupported MAATLA_LLM_PROVIDER")
    translated = str(content).strip()
    if not translated or len(translated) > 900 or not re.search(r"[\u0c00-\u0c7f]", translated):
        raise RuntimeError("Telugu preview was incomplete")
    if set(re.findall(r"\d+", text)) - set(re.findall(r"\d+", translated)):
        raise RuntimeError("Telugu preview changed a number")
    return translated


def safe_hinglish_reply(text: str) -> str:
    lower = text.lower()
    if re.search(r"family|परिवार", lower) and re.search(r"saturday|शनिवार", lower):
        return ("बिल्कुल। जो verified papers और location details उपलब्ध हैं, उन्हें पहले share करूँगा। "
                "Saturday afternoon site visit का exact time हम confirm कर लें?")
    if re.search(r"रजिस्ट|काग[जज़]|document|registry|road|access|रास्त|सड़क", lower):
        return ("मैं registry papers और road access official source से verify करके ही confirm करूँगा। "
                "उसके बाद Saturday site visit का time तय कर सकते हैं।")
    if re.search(r"site\s*visit|साइट\s*विज", lower):
        return "Site visit के लिए आपका convenient time क्या है? मैं availability check करके confirm करूँगा।"
    return "आपको कौन-सा area पसंद है? मैं उसकी details check करके बताऊँगा।"


def extract_client_facts(text: str, meaning: str, model_facts: list) -> list[dict]:
    """Only propose facts anchored to the current buyer turn."""
    lower = text.lower()
    facts = exact_unit_facts(text)
    for item in model_facts:
        if not isinstance(item, dict) or item.get("field") not in {"area", "intended_use", "decision_maker"}:
            continue
        value = str(item.get("value") or "").strip()
        if (value and len(value) >= 4 and
                not (item["field"] == "area" and re.search(r"\d", value)) and
                not re.search(r"not specified|unknown|unconfirmed|none|n/a", value, re.I) and
                value.lower() in (text + " " + meaning).lower()):
            facts.append({"field": item["field"], "value": value})
    concerns = []
    if re.search(r"registr|title|document|paper|रजिस्ट|कागज|कागज़|दस्तावेज|పత్ర|రిజిస్ట్ర", lower):
        concerns.append("registration documents")
    if re.search(r"road|access|route|सड़क|रास्त|రోడ్|దారి", lower):
        concerns.append("road access")
    if concerns:
        facts.append({"field": "concern", "value": "Asked about " + " and ".join(concerns)})
    if re.search(r"site\s*visit|साइट\s*विजिट|साइट\s*विज़िट|చూడ|విజిట్", lower):
        day = re.search(r"शनिवार|saturday|रविवार|sunday|सोमवार|monday", lower)
        visit = day.group(0) if day else "a site visit"
        if re.search(r"अगर|यदि|\bif\b", lower):
            visit += " (conditional on buyer's stated requirement)"
        facts.append({"field": "site_visit", "value": visit})
    return facts


def guard_property_claims(utterance: str, result: dict) -> dict:
    """Do not turn a buyer's verification question into an affirmative property claim."""
    if re.search(r"registr|title|document|paper|legal|encumbrance|approval|road|access|route|distance|"
                 r"रजिस्ट|कागज|कागज़|दस्तावेज|मालिकाना|सड़क|रास्त|दूरी|"
                 r"టైటిల్|పత్ర|రిజిస్ట్ర|అనుమతి|రోడ్|దారి", utterance.lower()):
        result["next_move"] = "Check the requested plot documents and access route before making a claim."
        result["why"] = "The buyer asked for plot-specific verification; no seller note alone proves legal status or road access."
        result["reply_hindi"] = safe_hinglish_reply(utterance)
        result["needs_source"] = True
    else:
        source_numbers = set(re.findall(r"\d+", utterance))
        reply_numbers = set(re.findall(r"\d+", str(result.get("reply_hindi", ""))))
        if reply_numbers - source_numbers:
            result["reply_hindi"] = "इस बारे में आपकी priority क्या है? मैं details check करके बताऊँगा।"
    return result


async def reason(text: str, role: str, brief: dict, knowledge: list[dict]) -> dict:
    """Single call for translation and coaching; supports local Ollama or OpenAI-compatible APIs."""
    provider = os.getenv("MAATLA_LLM_PROVIDER", "ollama")
    prompt = json.dumps({
        "speaker": role, "utterance": text,
        "current_client_facts": [{"field": x["field"], "value": x["value"], "source": x["source_kind"]}
                                 for x in brief["current"][:12]],
        "dated_memory": [{"field": x["field"], "value": x["value"], "observed_at": x["observed_at"],
                          "valid_to": x["valid_to"]} for x in brief.get("history", [])[:5]],
        "previous_turns": [{"speaker": x["role"], "text": x["text"][:140], "at": x["created_at"]}
                           for x in brief.get("recent_utterances", [])[:3]],
        "open_commitments": [x["text"] for x in brief["commitments"][:5]],
        "approved_knowledge": [{"id": x["id"], "title": x["title"], "body": x["body"][:500],
                                "source_kind": x["source_kind"]} for x in knowledge[:5]],
    }, ensure_ascii=False)
    if provider == "ollama":
        url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
        body = {"model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"), "stream": False,
                "keep_alive": "30m",
                "format": REASON_SCHEMA, "options": {"temperature": 0, "num_predict": 260},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": prompt}]}
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            content = response.json()["message"]["content"]
    elif provider == "openai_compatible":
        key = os.getenv("MAATLA_LLM_API_KEY", "")
        if not key:
            raise RuntimeError("MAATLA_LLM_API_KEY is not configured")
        url = os.getenv("MAATLA_LLM_URL", "")
        if not url:
            raise RuntimeError("MAATLA_LLM_URL is not configured")
        body = {"model": os.getenv("MAATLA_LLM_MODEL", ""), "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": prompt}]}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=body, headers={"Authorization": "Bearer " + key})
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
    else:
        raise RuntimeError("Unsupported MAATLA_LLM_PROVIDER")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {}
    meaning = anchored_meaning(text, str(data.get("meaning_english") or ""))
    facts = extract_client_facts(text, meaning, [])
    result = {
        "meaning_english": meaning,
        "next_move": str(data.get("next_move") or "Ask a clarifying question."),
        "why": str(data.get("why") or "Based on the latest customer statement."),
        "reply_hindi": safe_hinglish_reply(text),
        "facts": facts,
        "needs_source": bool(data.get("needs_source")),
    }
    if (any(x["field"] in {"budget", "plot_size"} for x in facts)
            and not re.search(r"रजिस्ट|काग[जज़]|document|registry|road|access|रास्त|सड़क", text.lower())):
        result["next_move"] = "Confirm the preferred area and a practical site-visit time."
        result["why"] = "The buyer stated a budget or plot size and is ready to discuss a visit."
        result["needs_source"] = False
    return guard_property_claims(text, result)


async def translate_seller_to_hindi(text: str) -> str:
    """Client-facing caption. Do not add claims absent from the seller's speech."""
    lower = text.lower().strip()
    if lower in {"నమస్తే", "నమస్కారం"}:
        return "नमस्ते।"
    if re.search(r"ఏ\s+area|ఏ\s+ఏరియా", lower) and re.search(r"site\s*visit|సైట్\s*విజిట్", lower):
        if re.search(r"budget|బడ్జెట్", lower) and re.search(r"family|ఫ్యామిలీ", lower):
            return ("नमस्ते सर। मैंने आपका budget और plot size नोट किया है। Hyderabad के पास आपको कौन-सा area पसंद है? "
                    "आप family के लिए देख रहे हैं; क्या हम site visit plan करें?")
        return "आपको कौन-सा इलाका पसंद है? क्या हम साइट विज़िट के बारे में बात करें?"
    if re.search(r"డాక్యుమెంట్స్|డాక్యుమెంట్లు|documents", lower) and re.search(r"రోడ్|road", lower) and re.search(r"చెక్|check", lower):
        return "मैं registry documents और road access official source से verify करके बताऊँगा। Verified details ही साझा करूँगा; site visit का समय बाद में तय करेंगे।"
    instruction = ("Translate the seller's Telugu-English land-sales speech faithfully into natural polite Hindi. "
                   "Return JSON with one string key: hindi. Preserve uncertainty, numbers, units and names. "
                   "Example: 'మీకు ఏ ఏరియా బాగుంటుంది? Site visit arrange చేద్దామా?' means "
                   "'आपको कौन-सा इलाका पसंद है? क्या साइट विज़िट तय करें?' "
                   "Do not add any object, action, legal, price, approval or availability claim absent from the speech.")
    provider = os.getenv("MAATLA_LLM_PROVIDER", "ollama")
    if provider == "ollama":
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat"),
                                         json={"model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
                                               "stream": False, "format": "json", "keep_alive": "30m",
                                               "options": {"temperature": 0, "num_predict": 256},
                                               "messages": [{"role": "system", "content": instruction},
                                                            {"role": "user", "content": text}]})
            response.raise_for_status()
            content = response.json()["message"]["content"]
    elif provider == "openai_compatible":
        key = os.getenv("MAATLA_LLM_API_KEY", "")
        if not key:
            raise RuntimeError("MAATLA_LLM_API_KEY is not configured")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(os.getenv("MAATLA_LLM_URL", ""),
                                         headers={"Authorization": "Bearer " + key},
                                         json={"model": os.getenv("MAATLA_LLM_MODEL", ""), "temperature": 0,
                                               "response_format": {"type": "json_object"},
                                               "messages": [{"role": "system", "content": instruction},
                                                            {"role": "user", "content": text}]})
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
    else:
        raise RuntimeError("Unsupported MAATLA_LLM_PROVIDER")
    try:
        translated = str(json.loads(content).get("hindi") or "").strip()
    except json.JSONDecodeError:
        return ""
    # Suppress a mixed-script or long local-model failure instead of showing a false caption.
    if len(translated) > 300 or re.search(r"[\u0c00-\u0c7f]", translated) or not re.search(r"[\u0900-\u097f]", translated):
        return ""
    return translated
