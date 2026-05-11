import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google import genai
from google.genai import types
from pydantic import BaseModel


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_file_hash(filepath: str) -> str:
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


def iter_kb_files(root_dir: str) -> list[Path]:
    root = Path(root_dir)
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        [
            p
            for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in {".md", ".txt", ".json"}
        ]
    )


def read_text_safely(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return ""


def fingerprint(content: str) -> str:
    lines = [line.strip().lower() for line in content.splitlines() if line.strip()]
    normalized = "\n".join(lines)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ─── Recall search ────────────────────────────────────────────────────────────

async def recall_search(query: str, limit: int = 8) -> list[dict]:
    api_key = os.environ.get("RECALL_API_KEY")
    mcp_url = os.environ.get("RECALL_MCP_URL", "https://backend.getrecall.ai/api/v1")
    base = mcp_url.rstrip("/")
    if base.endswith("/mcp"):
        base = base[:-4]

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30.0) as client:
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
    parts = []
    if card.get("title"):
        parts.append(card["title"])
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


class QueryRequest(BaseModel):
    mode: str
    query: str
    context: Optional[str] = None


class QueryResponse(BaseModel):
    mode: str
    result: str
    sources: list[str] = []
    verdict: Optional[str] = None


class IndexRequest(BaseModel):
    recall_dir: Optional[str] = None
    obsidian_dir: Optional[str] = None
    output_format: str = "json"


class FileUpdateRequest(BaseModel):
    root: str
    path: str
    content: Optional[str] = None
    new_path: Optional[str] = None
    tags: Optional[list[str]] = None


def build_index(root_dir: str, source: str) -> list[dict]:
    root = Path(root_dir)
    rows = []
    for file_path in iter_kb_files(root_dir):
        content = read_text_safely(file_path)
        rel = str(file_path.relative_to(root))
        stat = file_path.stat()
        rows.append(
            {
                "source": source,
                "path": rel,
                "name": file_path.stem,
                "ext": file_path.suffix.lower(),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "fingerprint": fingerprint(content),
                "preview": " ".join(content.split())[:240],
            }
        )
    return rows


def render_output(index_rows: list[dict], output_format: str) -> str:
    if output_format == "markdown":
        lines = ["# Knowledge Index", "", "| Source | Path | Modified | Fingerprint |", "|---|---|---|---|"]
        for row in index_rows:
            lines.append(f"| {row['source']} | `{row['path']}` | {row['modified']} | `{row['fingerprint']}` |")
        return "\n".join(lines)
    if output_format == "text":
        return "\n".join(f"[{r['source']}] {r['path']} ({r['modified']}) :: {r['fingerprint']}" for r in index_rows)
    return json.dumps(index_rows, indent=2)


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
        search_q = f"{req.context} {req.query}" if req.context else req.query
        cards = await recall_search(search_q)
        lore_fragments = "\n\n---\n\n".join(extract_card_text(c) for c in cards if extract_card_text(c))
        source_titles = [c.get("title", "Untitled") for c in cards if c.get("title")]
        if not lore_fragments:
            lore_fragments = "(No matching lore found in knowledge base)"
        context_line = f"Context (faction/location): {req.context}\n\n" if req.context else ""

        if req.mode == "image_prompt":
            result_text = await gemini_generate(IMAGE_PROMPT_SYSTEM, f"{context_line}Topic: {req.query}\n\nLore fragments:\n{lore_fragments}")
            return QueryResponse(mode=req.mode, result=result_text, sources=source_titles)
        if req.mode == "lore_hooks":
            result_text = await gemini_generate(LORE_HOOKS_SYSTEM, f"{context_line}Topic: {req.query}\n\nLore fragments:\n{lore_fragments}")
            return QueryResponse(mode=req.mode, result=result_text, sources=source_titles)

        raw = await gemini_generate(AUDIT_SYSTEM, f"EXISTING LORE FRAGMENTS:\n{lore_fragments}\n\nNEW DATA TO AUDIT:\n{req.query}")
        import re
        verdict = "UNKNOWN"
        try:
            clean = re.sub(r"```(?:json)?|```", "", raw).strip()
            parsed = json.loads(clean)
            verdict = parsed.get("verdict", "UNKNOWN")
        except Exception:
            pass
        return QueryResponse(mode=req.mode, result=raw, sources=source_titles, verdict=verdict)

    @api.post("/kb/index")
    def kb_index(req: IndexRequest):
        recall_dir = req.recall_dir or os.environ.get("RECALL_EXPORT_DIR", "./data/recall")
        obsidian_dir = req.obsidian_dir or os.environ.get("OBSIDIAN_DIR", "./data/obsidian")
        output_format = req.output_format.lower()
        if output_format not in {"json", "markdown", "text"}:
            return JSONResponse({"error": "output_format must be json, markdown, or text"}, status_code=400)

        recall_index = build_index(recall_dir, "recall")
        obsidian_index = build_index(obsidian_dir, "obsidian")
        combined = sorted(recall_index + obsidian_index, key=lambda x: (x["source"], x["path"]))
        fp_obsidian = {row["fingerprint"] for row in obsidian_index}
        pending = [row for row in recall_index if row["fingerprint"] not in fp_obsidian]

        return {
            "format": output_format,
            "summary": {
                "recall_count": len(recall_index),
                "obsidian_count": len(obsidian_index),
                "pending_migration_count": len(pending),
            },
            "index": render_output(combined, output_format),
            "pending": pending,
        }

    @api.post("/kb/file")
    def kb_file_update(req: FileUpdateRequest):
        root = Path(req.root).resolve()
        path = (root / req.path).resolve()
        if root not in path.parents and path != root:
            return JSONResponse({"error": "Invalid path outside root"}, status_code=400)

        if req.content is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            final_content = req.content
            if req.tags and path.suffix.lower() == ".md":
                tag_line = " ".join(f"#{tag.strip().replace(' ', '-') }" for tag in req.tags if tag.strip())
                final_content = f"{tag_line}\n\n{final_content}" if tag_line else final_content
            path.write_text(final_content, encoding="utf-8")

        if req.new_path:
            new_abs = (root / req.new_path).resolve()
            if root not in new_abs.parents and new_abs != root:
                return JSONResponse({"error": "Invalid new_path outside root"}, status_code=400)
            new_abs.parent.mkdir(parents=True, exist_ok=True)
            path.rename(new_abs)
            path = new_abs

        return {"ok": True, "path": str(path.relative_to(root))}

    app = FastAPI()
    app.include_router(api, prefix="/api")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        css_hash = get_file_hash(os.path.join(static_dir, "styles.css"))
        js_hash = get_file_hash(os.path.join(static_dir, "app.js"))
        return templates.TemplateResponse(request, "index.html", {"css_hash": css_hash, "js_hash": js_hash})

    app.mount("/static", StaticFiles(directory=static_dir), name="ui")
    return app
