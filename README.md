# Data App

This FastAPI app provides:
- Existing lore querying workflows (Recall + Gemini).
- A **Knowledge Base Governance layer** for Recall ⇄ Obsidian indexing, migration diffing, and file edits.

## Run

```bash
./start.sh
```

## New API tools

### `POST /api/kb/index`
Builds a current index for Recall + Obsidian (`.md`, `.txt`, `.json` files), returns:
- Summary counts.
- Full index output (`json`, `markdown`, or `text`).
- `pending` delta list = Recall files not yet migrated to Obsidian (fingerprint-based).

Request body:

```json
{
  "recall_dir": "./data/recall",
  "obsidian_dir": "./data/obsidian",
  "output_format": "markdown"
}
```

### `POST /api/kb/file`
Agent-friendly file operation endpoint for KB editing workflows:
- Write/update file content.
- Optional Markdown tags injection.
- Rename/move file.

Request body:

```json
{
  "root": "./data/obsidian",
  "path": "drafts/topic.md",
  "content": "# Updated note",
  "tags": ["lore", "canon"],
  "new_path": "canon/topic.md"
}
```

## CLI tool

Use `scripts/kb_tools.py` to run indexing/delta directly from terminal:

```bash
python scripts/kb_tools.py \
  --recall-dir ./data/recall \
  --obsidian-dir ./data/obsidian \
  --format json \
  --output ./kb_index.json
```

## Environment variables

- `RECALL_API_KEY`
- `RECALL_MCP_URL` (default: `https://backend.getrecall.ai/api/v1`)
- `MGEMCO_GOOGLE_API_KEY`
- `RECALL_EXPORT_DIR` (default fallback for `/api/kb/index`)
- `OBSIDIAN_DIR` (default fallback for `/api/kb/index`)
