"""
AI extraction runner — iterate over unprocessed filings, call Claude API,
parse structured output, and write results to filing_extractions.

Source: ARCHITECTURE.md Phase 2 spec.

Usage:
    python -m ai_agent.runner                    # process all unprocessed filings
    python -m ai_agent.runner --limit 5          # process at most 5 filings
    python -m ai_agent.runner --filing-id 42     # process a specific filing

Model: claude-sonnet-4-6 (per ARCHITECTURE.md section 3, Layer 2 spec).
"""

import os
import json
import argparse
import traceback
from pathlib import Path

import anthropic
import psycopg2
from dotenv import load_dotenv

from ai_agent.parser import validate_fundamental, validate_supply_chain

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
MODEL = "claude-sonnet-4-6"
MAX_FILING_CHARS = 150_000  # truncate very large filings to stay within context

# Map form_type to extraction types and their prompt files
EXTRACTION_CONFIG = {
    "10-K": [
        {"type": "fundamental", "prompt_file": "fundamental_extraction.txt"},
        {"type": "supply_chain", "prompt_file": "supply_chain_extraction.txt"},
    ],
    "10-Q": [
        {"type": "fundamental", "prompt_file": "fundamental_extraction.txt"},
        {"type": "supply_chain", "prompt_file": "supply_chain_extraction.txt"},
    ],
}


# ── Prompt Loading ───────────────────────────────────────────────────────────

_prompt_cache = {}


def load_prompt(prompt_file):
    """Load a prompt template from disk, with caching."""
    if prompt_file not in _prompt_cache:
        path = PROMPTS_DIR / prompt_file
        _prompt_cache[prompt_file] = path.read_text(encoding="utf-8")
    return _prompt_cache[prompt_file]


# ── Filing Reading ───────────────────────────────────────────────────────────

FRONT_CHARS = 50_000   # cover cover page, business description, MD&A
BACK_CHARS = 100_000   # financial statements, notes, risk factors, supply chain disclosures


def read_filing(filepath):
    """Read a filing from disk. Returns the text content, truncated if needed.

    If the filing exceeds MAX_FILING_CHARS, keeps the first 50k and last 100k
    characters. Financial statements and supply chain disclosures are typically
    in the back half of 10-Ks.
    """
    full_path = BASE_DIR / filepath
    if not full_path.exists():
        raise FileNotFoundError(f"Filing not found: {full_path}")

    content = full_path.read_text(encoding="utf-8", errors="replace")

    if len(content) > MAX_FILING_CHARS:
        front = content[:FRONT_CHARS]
        back = content[-BACK_CHARS:]
        content = (
            front
            + "\n\n[... MIDDLE TRUNCATED — "
            + f"{len(content) - FRONT_CHARS - BACK_CHARS:,} characters removed ...]\n\n"
            + back
        )

    return content


# ── Claude API ───────────────────────────────────────────────────────────────

def call_claude(system_prompt, filing_text):
    """Call Claude API with the system prompt and filing text.

    Returns (parsed_json, tokens_used) or raises on failure.
    """
    client = anthropic.Anthropic()

    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[
            {"role": "user", "content": filing_text},
        ],
    )

    # Extract text from response
    response_text = ""
    for block in message.content:
        if block.type == "text":
            response_text += block.text

    tokens_used = message.usage.input_tokens + message.usage.output_tokens

    # Parse JSON from response — strip any accidental markdown fencing
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        # Remove ```json ... ``` wrapping
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    parsed = json.loads(cleaned)
    return parsed, tokens_used


# ── Database ─────────────────────────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def get_unprocessed_filings(cur, limit=None, filing_id=None):
    """Return list of unprocessed filing rows."""
    if filing_id:
        cur.execute(
            """
            SELECT id, company_id, ticker, form_type, filepath, accession_number
            FROM filings WHERE id = %s
            """,
            (filing_id,),
        )
    else:
        if limit:
            cur.execute(
                """
                SELECT id, company_id, ticker, form_type, filepath, accession_number
                FROM filings WHERE processed = FALSE
                ORDER BY filed_date ASC
                LIMIT %s
                """,
                (limit,),
            )
        else:
            cur.execute(
                """
                SELECT id, company_id, ticker, form_type, filepath, accession_number
                FROM filings WHERE processed = FALSE
                ORDER BY filed_date ASC
                """
            )

    columns = ["id", "company_id", "ticker", "form_type", "filepath", "accession_number"]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def insert_extraction(cur, filing_id, extraction_type, extracted_json, tokens_used):
    """Insert an extraction result into filing_extractions."""
    cur.execute(
        """
        INSERT INTO filing_extractions
            (filing_id, extraction_type, extracted_json, model_used, tokens_used)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            filing_id,
            extraction_type,
            json.dumps(extracted_json),
            MODEL,
            tokens_used,
        ),
    )


def mark_processed(cur, filing_id):
    """Set processed = TRUE on a filing."""
    cur.execute(
        "UPDATE filings SET processed = TRUE WHERE id = %s",
        (filing_id,),
    )


# ── Processing ───────────────────────────────────────────────────────────────

def process_filing(conn, filing):
    """Run all extraction types for a single filing.

    Returns (success_count, error_count) for extractions attempted.
    """
    filing_id = filing["id"]
    ticker = filing["ticker"] or "?"
    form_type = filing["form_type"]
    filepath = filing["filepath"]

    extractions = EXTRACTION_CONFIG.get(form_type, [])
    if not extractions:
        print(f"  [{ticker}] No extraction config for form_type={form_type}, skipping.")
        return 0, 0

    # Read filing from disk
    try:
        filing_text = read_filing(filepath)
    except FileNotFoundError as e:
        print(f"  [{ticker}] ERROR: {e}")
        return 0, 1

    cur = conn.cursor()
    successes = 0
    errors = 0

    for config in extractions:
        extraction_type = config["type"]
        prompt_file = config["prompt_file"]

        try:
            system_prompt = load_prompt(prompt_file)
            print(f"  [{ticker}] Running {extraction_type} extraction "
                  f"(filing {filing_id})...")

            parsed, tokens_used = call_claude(system_prompt, filing_text)

            # Validate before writing to DB
            if extraction_type == "fundamental":
                is_valid, err_msg = validate_fundamental(parsed)
                if not is_valid:
                    errors += 1
                    print(f"  [{ticker}] {extraction_type}: validation failed — {err_msg}")
                    continue
            elif extraction_type == "supply_chain":
                cleaned, skipped_count, skip_reasons = validate_supply_chain(parsed)
                if skipped_count > 0:
                    print(f"  [{ticker}] {extraction_type}: "
                          f"filtered {skipped_count} invalid item(s)")
                    for reason in skip_reasons:
                        print(f"    - {reason}")
                parsed = cleaned

            insert_extraction(cur, filing_id, extraction_type, parsed, tokens_used)
            successes += 1
            print(f"  [{ticker}] {extraction_type}: OK ({tokens_used} tokens)")

        except json.JSONDecodeError as e:
            errors += 1
            print(f"  [{ticker}] {extraction_type}: JSON parse error — {e}")
        except anthropic.APIError as e:
            errors += 1
            print(f"  [{ticker}] {extraction_type}: Claude API error — {e}")
        except Exception as e:
            errors += 1
            print(f"  [{ticker}] {extraction_type}: unexpected error — {e}")
            traceback.print_exc()

    # Mark filing as processed only if all extractions succeeded
    if errors == 0:
        mark_processed(cur, filing_id)

    conn.commit()
    cur.close()
    return successes, errors


def run(conn, limit=None, filing_id=None, dry_run=False):
    """Process all unprocessed filings (or a specific one).

    Returns (total_successes, total_errors, filings_processed).
    """
    cur = conn.cursor()
    filings = get_unprocessed_filings(cur, limit=limit, filing_id=filing_id)
    cur.close()

    if not filings:
        print("No unprocessed filings found.")
        return 0, 0, 0

    print(f"Found {len(filings)} filing(s) to process.\n")

    if dry_run:
        for f in filings:
            extractions = EXTRACTION_CONFIG.get(f["form_type"], [])
            types = ", ".join(e["type"] for e in extractions)
            print(f"  [DRY RUN] {f['ticker'] or '?'} | "
                  f"filing {f['id']} | {f['form_type']} | extractions: {types}")
        est_calls = sum(len(EXTRACTION_CONFIG.get(f["form_type"], [])) for f in filings)
        print(f"\n{len(filings)} filings, ~{est_calls} API calls. "
              f"No API calls made (--dry-run).")
        return 0, 0, 0

    total_successes = 0
    total_errors = 0

    for filing in filings:
        successes, errors = process_filing(conn, filing)
        total_successes += successes
        total_errors += errors

    return total_successes, total_errors, len(filings)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run AI extraction on unprocessed EDGAR filings."
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Maximum number of filings to process.",
    )
    parser.add_argument(
        "--filing-id", type=int, default=None,
        help="Process a specific filing by ID (ignores --limit).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List filings that would be processed without calling the API.",
    )
    args = parser.parse_args()

    if not args.dry_run:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env file."
            )

    conn = get_connection()
    try:
        successes, errors, count = run(
            conn, limit=args.limit, filing_id=args.filing_id,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            print(f"\nDone. {count} filing(s) processed: "
                  f"{successes} extraction(s) succeeded, {errors} failed.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
