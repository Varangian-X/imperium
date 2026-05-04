import hashlib
import os
import httpx
from typing import Optional

from fastapi import FastAPI, APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from google import genai
from google.genai import types


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_file_hash(filepath: str) -> str:
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


# ─── Recall search ────────────────────────────────────────────────────────────

async def recall_search(query: str, limit: int = 8) -> list[dict]:
    """Search the Recall knowledge base and return matching cards."""
    api_key = os.environ.get("RECALL_API_KEY")
    mcp_url = os.environ.get("RECALL_MCP_URL", "https://backend.getrecall.ai/api/v1")

    # Normalise base URL — strip trailing slash and /mcp suffix if present
    base = mcp_url.rstrip("/")
    if base.endswith("/mcp"):
        base = base[:-4]

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Try semantic search first
        try:
            resp = await client.post(
                f"{base}/cards/search",
                headers=headers,
                json={"q": query, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            cards = data if isinstance(data, list) else data.get("cards", data.get("results", []))
            return cards[:limit]
        except Exception:
            pass

        # Fallback: list cards with keyword filter
        try:
            resp = await client.get(
                f"{base}/cards",
                headers=headers,
                params={"q": query, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            cards = data if isinstance(data, list) else data.get("cards", data.get("results", []))
            return cards[:limit]
        except Exception:
            return []


def extract_card_text(card: dict) -> str:
    """Pull readable text from a Recall card object."""
    parts = []
    if card.get("title"):
        parts.append(card["title"])
    # content may be a string or list of chunks
    content = card.get("content") or card.get("summary") or card.get("text") or ""
    if isinstance(content, list):
        for chunk in content:
            if isinstance(chunk, dict):
                parts.append(chunk.get("text", ""))
            else:
                parts.append(str(chunk))
    elif isinstance(content, str):
        parts.append(content)
    return "\n".join(p for p in parts if p).strip()


# ─── Gemini synthesis ─────────────────────────────────────────────────────────

def get_gemini_client() -> genai.Client:
    api_key = os.environ.get("MGEMCO_GOOGLE_API_KEY")
    return genai.Client(api_key=api_key)


async def gemini_generate(system_prompt: str, user_prompt: str) -> str:
    client = get_gemini_client()
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=system_prompt),
        contents=user_prompt,
    )
    return response.text.strip()


# ─── Mode prompts ─────────────────────────────────────────────────────────────

IMAGE_PROMPT_SYSTEM = """You are a visual art director for a dark, high-fantasy world called the Sovereign Forge.
Given lore fragments from the knowledge base, produce a concise IMAGE PROMPT FRAGMENT (max 120 words) suitable for AI image generation.
Focus on: visual style, lighting, colour palette, key visual elements, mood, and faction/location aesthetics.
Output ONLY the prompt fragment — no preamble, no explanation."""

LORE_HOOKS_SYSTEM = """You are a narrative designer for the Sovereign Forge universe.
Given lore fragments from the knowledge base and an input topic, produce 3–5 LORE & NARRATIVE HOOKS.
Each hook should be a single evocative sentence that could seed a story, quest, or world-building detail.
Format as a numbered list. No preamble."""

AUDIT_SYSTEM = """You are a strict lore continuity auditor for the Sovereign Forge universe.
Given existing lore fragments from the knowledge base and NEW DATA submitted by the user, determine whether the new data CONTRADICTS any established lore.
Respond in this exact JSON format:
{
  "verdict": "PASS" | "CONTRADICTION",
  "confidence": "high" | "medium" | "low",
  "issues": ["<issue 1>", "<issue 2>"],
  "notes": "<brief explanation>"
}
If no contradiction, issues array is empty. Be precise and cite specific conflicts."""


# ─── Request / Response models ────────────────────────────────────────────────

class QueryRequest(BaseModel):
    mode: str          # "image_prompt" | "lore_hooks" | "audit"
    query: str         # main input text
    context: Optional[str] = None   # faction / location / extra context


class QueryResponse(BaseModel):
    mode: str
    result: str
    sources: list[str] = []
    verdict: Optional[str] = None   # for audit mode: "PASS" | "CONTRADICTION"


# ─── App factory ──────────────────────────────────────────────────────────────

def create_app(static_dir: str) -> FastAPI:
    api = APIRouter()
    templates = Jinja2Templates(directory=static_dir)

    @api.get("/health")
    def health():
        return {"ok": True}

    @api.post("/query")
    async def query_lore(req: QueryRequest):
        if req.mode not in ("image_prompt", "lore_hooks", "audit"):
            return JSONResponse({"error": "Invalid mode"}, status_code=400)

        # 1. Build search query
        search_q = req.query
        if req.context:
            search_q = f"{req.context} {req.query}"

        # 2. Fetch lore from Recall
        cards = await recall_search(search_q)
        lore_fragments = "\n\n---\n\n".join(
            extract_card_text(c) for c in cards if extract_card_text(c)
        )
        source_titles = [c.get("title", "Untitled") for c in cards if c.get("title")]

        if not lore_fragments:
            lore_fragments = "(No matching lore found in knowledge base)"

        # 3. Build user prompt for Gemini
        context_line = f"Context (faction/location): {req.context}\n\n" if req.context else ""

        if req.mode == "image_prompt":
            user_prompt = (
                f"{context_line}"
                f"Topic: {req.query}\n\n"
                f"Lore fragments:\n{lore_fragments}"
            )
            result_text = await gemini_generate(IMAGE_PROMPT_SYSTEM, user_prompt)
            return QueryResponse(mode=req.mode, result=result_text, sources=source_titles)

        elif req.mode == "lore_hooks":
            user_prompt = (
                f"{context_line}"
                f"Topic: {req.query}\n\n"
                f"Lore fragments:\n{lore_fragments}"
            )
            result_text = await gemini_generate(LORE_HOOKS_SYSTEM, user_prompt)
            return QueryResponse(mode=req.mode, result=result_text, sources=source_titles)

        elif req.mode == "audit":
            user_prompt = (
                f"EXISTING LORE FRAGMENTS:\n{lore_fragments}\n\n"
                f"NEW DATA TO AUDIT:\n{req.query}"
            )
            raw = await gemini_generate(AUDIT_SYSTEM, user_prompt)

            # Parse JSON from Gemini response
            import json, re
            verdict = "UNKNOWN"
            try:
                # Strip markdown code fences if present
                clean = re.sub(r"```(?:json)?|```", "", raw).strip()
                parsed = json.loads(clean)
                verdict = parsed.get("verdict", "UNKNOWN")
                result_text = raw
            except Exception:
                result_text = raw

            return QueryResponse(
                mode=req.mode,
                result=result_text,
                sources=source_titles,
                verdict=verdict,
            )

    app = FastAPI()
    app.include_router(api, prefix="/api")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        css_hash = get_file_hash(os.path.join(static_dir, "styles.css"))
        js_hash = get_file_hash(os.path.join(static_dir, "app.js"))
        return templates.TemplateResponse(
            request, "index.html", {"css_hash": css_hash, "js_hash": js_hash}
        )

    app.mount("/static", StaticFiles(directory=static_dir), name="ui")
    return app
