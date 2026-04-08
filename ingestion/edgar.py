"""
EDGAR filing downloader — pull 10-K and 10-Q filings for the starting universe.

Uses the SEC EDGAR submissions API (no API key required).
Stores raw filing text in data/filings/<TICKER>/<form_type>/.
Writes metadata to the filings table.

Usage:
    python -m ingestion.edgar              # download all companies in DB
    python -m ingestion.edgar --ticker ETN  # download for one ticker

Rate limit: SEC requires <= 10 req/sec. We use 200ms between requests.
"""

import os
import time
import argparse
from pathlib import Path

import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
FILINGS_DIR = BASE_DIR / "data" / "filings"
REQUEST_DELAY = 0.2  # 200ms between requests (SEC limit: 10/sec)

USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT",
    "EquityPro Research contact@equitypro.io",
)
HEADERS = {"User-Agent": USER_AGENT}

FORM_TYPES = ["10-K", "10-Q"]

# ── CIK Lookup ───────────────────────────────────────────────────────────────

_cik_cache = {}


def load_cik_map():
    """Download the SEC company_tickers.json and build a ticker→CIK map."""
    global _cik_cache
    if _cik_cache:
        return _cik_cache

    url = "https://www.sec.gov/files/company_tickers.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    for entry in data.values():
        ticker = entry["ticker"].upper()
        cik = int(entry["cik_str"])
        _cik_cache[ticker] = cik

    print(f"Loaded {len(_cik_cache)} tickers from SEC CIK map.")
    return _cik_cache


def get_cik(ticker):
    """Return the 10-digit zero-padded CIK string for a ticker, or None."""
    cik_map = load_cik_map()
    cik = cik_map.get(ticker.upper())
    if cik is None:
        return None
    return str(cik).zfill(10)


# ── Submissions API ──────────────────────────────────────────────────────────

def fetch_submissions(cik_padded):
    """Fetch filing metadata from the SEC submissions API for a given CIK.

    Returns list of dicts with keys:
        accession_number, form_type, filing_date, primary_document,
        period_of_report (if available)
    """
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    time.sleep(REQUEST_DELAY)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return []

    accessions = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])
    periods = recent.get("reportDate", [])

    filings = []
    for i in range(len(accessions)):
        form = forms[i] if i < len(forms) else ""
        if form not in FORM_TYPES:
            continue
        filings.append({
            "accession_number": accessions[i],
            "form_type": form,
            "filing_date": dates[i] if i < len(dates) else None,
            "primary_document": docs[i] if i < len(docs) else None,
            "period_of_report": periods[i] if i < len(periods) else None,
        })

    # Also load older filings from supplemental files if present
    for extra_file in data.get("filings", {}).get("files", []):
        extra_url = f"https://data.sec.gov/submissions/{extra_file['name']}"
        time.sleep(REQUEST_DELAY)
        extra_resp = requests.get(extra_url, headers=HEADERS, timeout=30)
        if extra_resp.status_code != 200:
            continue
        extra_data = extra_resp.json()
        ex_accessions = extra_data.get("accessionNumber", [])
        ex_forms = extra_data.get("form", [])
        ex_dates = extra_data.get("filingDate", [])
        ex_docs = extra_data.get("primaryDocument", [])
        ex_periods = extra_data.get("reportDate", [])
        for i in range(len(ex_accessions)):
            form = ex_forms[i] if i < len(ex_forms) else ""
            if form not in FORM_TYPES:
                continue
            filings.append({
                "accession_number": ex_accessions[i],
                "form_type": form,
                "filing_date": ex_dates[i] if i < len(ex_dates) else None,
                "primary_document": ex_docs[i] if i < len(ex_docs) else None,
                "period_of_report": ex_periods[i] if i < len(ex_periods) else None,
            })

    return filings


# ── Filing Download ──────────────────────────────────────────────────────────

def download_filing(cik_padded, filing):
    """Download a single filing document. Returns the local filepath or None."""
    accession = filing["accession_number"]
    primary_doc = filing["primary_document"]
    if not primary_doc:
        return None

    accession_nodashes = accession.replace("-", "")
    url = (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik_padded)}/{accession_nodashes}/{primary_doc}"
    )

    time.sleep(REQUEST_DELAY)
    resp = requests.get(url, headers=HEADERS, timeout=60)
    if resp.status_code != 200:
        print(f"  Failed to download {url} (HTTP {resp.status_code})")
        return None

    return resp.text


def save_filing(ticker, filing, content):
    """Save filing content to disk. Returns the relative filepath."""
    form_dir = FILINGS_DIR / ticker.upper() / filing["form_type"]
    form_dir.mkdir(parents=True, exist_ok=True)

    # Use accession number as filename (safe for filesystem)
    safe_name = filing["accession_number"].replace("-", "_")
    ext = Path(filing["primary_document"]).suffix or ".htm"
    filename = f"{safe_name}{ext}"
    filepath = form_dir / filename

    filepath.write_text(content, encoding="utf-8")
    return str(filepath.relative_to(BASE_DIR))


# ── Database Operations ──────────────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def filing_exists(cur, company_id, accession_number):
    """Check if a filing is already stored in the DB."""
    cur.execute(
        "SELECT id FROM filings WHERE company_id = %s AND filename = %s",
        (company_id, accession_number),
    )
    return cur.fetchone() is not None


def insert_filing(cur, company_id, ticker, filing, filepath):
    """Insert a filing record into the filings table."""
    cur.execute(
        """
        INSERT INTO filings
            (company_id, ticker, form_type, filed_date, period_of_report,
             filename, filepath, processed)
        VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE)
        """,
        (
            company_id,
            ticker,
            filing["form_type"],
            filing["filing_date"],
            filing["period_of_report"],
            filing["accession_number"],
            filepath,
        ),
    )


# ── Main Orchestration ───────────────────────────────────────────────────────

def download_for_company(conn, company_id, ticker):
    """Download all 10-K and 10-Q filings for a single company.

    Returns (downloaded_count, skipped_count).
    """
    cik = get_cik(ticker)
    if cik is None:
        print(f"  {ticker}: no CIK found (non-US or private), skipping.")
        return 0, 0

    print(f"  {ticker}: CIK={cik}, fetching submissions...")
    filings = fetch_submissions(cik)
    print(f"  {ticker}: found {len(filings)} 10-K/10-Q filings.")

    cur = conn.cursor()
    downloaded = 0
    skipped = 0

    for filing in filings:
        if filing_exists(cur, company_id, filing["accession_number"]):
            skipped += 1
            continue

        content = download_filing(cik, filing)
        if content is None:
            continue

        filepath = save_filing(ticker, filing, content)
        insert_filing(cur, company_id, ticker, filing, filepath)
        downloaded += 1

    conn.commit()
    cur.close()
    return downloaded, skipped


def download_all(conn, ticker_filter=None):
    """Download filings for all public US companies in the database.

    Non-US listed companies (PRY.MI, NEX.PA, NKT.CO, S92.DE) and private
    companies are skipped — EDGAR does not have their filings.
    See ARCHITECTURE.md section 9.
    """
    cur = conn.cursor()
    if ticker_filter:
        cur.execute(
            "SELECT id, ticker FROM companies WHERE ticker = %s AND is_public = TRUE",
            (ticker_filter,),
        )
    else:
        cur.execute(
            "SELECT id, ticker FROM companies WHERE ticker IS NOT NULL AND is_public = TRUE"
        )
    companies = cur.fetchall()
    cur.close()

    total_downloaded = 0
    total_skipped = 0

    for company_id, ticker in companies:
        # Skip non-US tickers (contain a dot indicating exchange suffix)
        if "." in ticker:
            print(f"  {ticker}: non-US listing, skipping EDGAR download.")
            continue

        downloaded, skipped = download_for_company(conn, company_id, ticker)
        total_downloaded += downloaded
        total_skipped += skipped
        print(f"  {ticker}: {downloaded} downloaded, {skipped} already existed.")

    return total_downloaded, total_skipped


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download 10-K and 10-Q filings from SEC EDGAR."
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Download filings for a single ticker only.",
    )
    args = parser.parse_args()

    conn = get_connection()
    try:
        downloaded, skipped = download_all(conn, ticker_filter=args.ticker)
        print(f"\nDone. {downloaded} filings downloaded, {skipped} skipped (already existed).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
