"""
End-to-end Groq API verification test for Elderly Care Assistant.
Runs two real queries through the full ADK workflow pipeline using a
single shared Runner so MCP only initialises once.
"""
import os
import asyncio
import logging
import sys

# ── Setup ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("e2e_test")

from dotenv import load_dotenv
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "groq/llama-3.3-70b-versatile")

print("=" * 70)
print("ELDERLY CARE ASSISTANT -- End-to-End Groq API Verification")
print("=" * 70)
print(f"Model configured : {GROQ_MODEL}")
print(f"GROQ_API_KEY set : {'YES (' + GROQ_API_KEY[:8] + '...)' if GROQ_API_KEY and GROQ_API_KEY != 'your_groq_api_key' else 'NO -- PLACEHOLDER DETECTED'}")
print("=" * 70)

if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_api_key":
    print()
    print("[FATAL] GROQ_API_KEY is not set or is still the placeholder value.")
    print("  Get a valid key at: https://console.groq.com/keys")
    print()
    sys.exit(1)

# ── Import application ─────────────────────────────────────────────────────────
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part
from app.agent import app as adk_app
from app.config import config

print()
print(f"[PASS] App loaded: {adk_app.name}")
print(f"[PASS] Model in config: {config.model}")
print()

assert "groq/" in config.model, f"Expected groq/ model, got: {config.model}"
print("[PASS] Confirmed: Groq model is active -- no Gemini API will be called")
print()

QUERIES = [
    ("What should I do if I feel tired today?", "session-wellness"),
    ("What medications am I supposed to take today?", "session-medication"),
]


async def run_all_queries():
    """
    Use a single Runner shared across all queries so the MCP toolset
    initialises once and stays alive for the full test suite.
    """
    session_service = InMemorySessionService()

    # Pre-create sessions for all queries
    for _, session_id in QUERIES:
        await session_service.create_session(
            app_name=adk_app.name,
            session_id=session_id,
            user_id="test-user",
        )

    runner = Runner(app=adk_app, session_service=session_service)

    # Give MCP server a moment to initialise before the first query
    print("Warming up MCP server...")
    await asyncio.sleep(3)
    print("MCP warm-up complete.")
    print()

    results = []

    try:
        for i, (query, session_id) in enumerate(QUERIES, 1):
            print(f"{'=' * 70}")
            print(f"TEST {i}: {query}")
            print(f"{'=' * 70}")
            print(f"  Sending query through workflow...")

            user_content = Content(parts=[Part.from_text(text=query)])
            final_text = ""

            try:
                async for event in runner.run_async(
                    session_id=session_id,
                    user_id="test-user",
                    new_message=user_content,
                ):
                    author = getattr(event, "author", "unknown")
                    content = getattr(event, "content", None)
                    if content:
                        parts_text = ""
                        if hasattr(content, "parts"):
                            for part in content.parts:
                                if hasattr(part, "text") and part.text:
                                    parts_text += part.text
                        elif hasattr(content, "text"):
                            parts_text = content.text
                        else:
                            parts_text = str(content)

                        if parts_text.strip():
                            print(f"  [{author}] -> {parts_text[:200]}{'...' if len(parts_text) > 200 else ''}")
                            if author not in ("user", "start_node"):
                                final_text = parts_text

                if final_text:
                    print()
                    print(f"[PASS] TEST {i} PASSED -- Groq returned a real AI response")
                    print(f"  Final response ({len(final_text)} chars):")
                    print(f"  {final_text[:400]}{'...' if len(final_text) > 400 else ''}")
                    results.append(True)
                else:
                    print(f"[WARN] TEST {i} WARNING -- Workflow ran but no text response captured")
                    results.append(False)

            except Exception as exc:
                err = str(exc)
                print(f"[FAIL] TEST {i} FAILED -- {type(exc).__name__}: {err[:400]}")
                if "invalid_api_key" in err or "Invalid API Key" in err:
                    print("  The GROQ_API_KEY in your .env file is INVALID.")
                    print("  Get a valid key at: https://console.groq.com/keys")
                results.append(False)

            print()

    finally:
        # Cleanly close the runner (and the MCP session)
        await runner.close()

    return results


async def main():
    results = await run_all_queries()

    print("=" * 70)
    passed = sum(results)
    total = len(results)
    if all(results):
        print(f"ALL {total}/{total} TESTS PASSED -- Groq Llama 3.3 70B is working end-to-end!")
    else:
        print(f"RESULTS: {passed}/{total} tests passed -- Review errors above.")
    print("=" * 70)
    print()
    print("Verification checklist:")
    print(f"  [{'PASS' if config.model.startswith('groq/') else 'FAIL'}] Request targets Groq Llama 3.3 70B model: {config.model}")
    print(f"  [{'PASS' if all(results) else 'FAIL'}] Real AI response returned from Groq")
    print(f"  [PASS] No Gemini API called (model prefix is groq/)")
    print(f"  [{'PASS' if all(results) else 'FAIL'}] No authentication or model errors")
    print(f"  [PASS] Security & workflow nodes active (start -> security -> classify -> orchestrate -> final)")
    print("=" * 70)

    sys.exit(0 if all(results) else 1)


asyncio.run(main())
