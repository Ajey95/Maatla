"""One real credential-backed TTS->STT roundtrip. Never prints or saves credentials."""
import asyncio
import json
import subprocess
import sys

from app.main import app  # loads ignored local .env
from app.providers import GnaniSTT, synthesize_hindi


async def main():
    seen = []

    async def received(text, language):
        seen.append((text, language))

    phrase = ("मेरा budget 80 lakh है, Saturday को site visit कर सकते हैं क्या?"
              if "--mixed" in sys.argv else "नमस्ते, शनिवार को साइट विजिट कर सकते हैं क्या?")
    audio = await synthesize_hindi(phrase)
    converted = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "pipe:1"],
        input=audio, capture_output=True, check=True).stdout
    stream = GnaniSTT("buyer", received)
    await stream.start()
    await asyncio.sleep(0.5)
    if stream.task and stream.task.done():
        raise RuntimeError("STT setup response: " + str(stream.task.exception()))
    try:
        padded = converted + b"\0" * (1024 - len(converted) % 1024) + b"\0" * 32768
        for i in range(0, len(padded), 1024):
            await stream.send(padded[i:i + 1024])
            await asyncio.sleep(0.032)
        await asyncio.sleep(2)
    finally:
        await stream.close()
    print(json.dumps({"tts_bytes": len(audio), "stt_transcripts": seen}, ensure_ascii=True))
    if not seen:
        raise SystemExit("No transcript returned")


if __name__ == "__main__":
    asyncio.run(main())
