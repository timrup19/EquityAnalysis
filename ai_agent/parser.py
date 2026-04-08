"""
Validation functions for AI extraction output.

Validates structured JSON returned by Claude before writing to the database.
Each validator returns a clear result so the runner can decide whether to
store, filter, or skip the extraction.
"""

VALID_RELATIONSHIP_TYPES = frozenset([
    "sole_source",
    "preferred_supplier",
    "commodity_supplier",
    "capacity_dependency",
])


# ── Fundamental extraction validation ────────────────────────────────────────

def validate_fundamental(data):
    """Validate parsed JSON from fundamental_extraction prompt.

    Checks:
    - data is a dict
    - has at least one revenue segment with a segment_name
    - has a gross_margin field that is a dict
    - has a free_cash_flow field that is a dict

    Returns:
        (is_valid, error_message)
        is_valid: True if data passes minimum checks.
        error_message: None if valid, otherwise a description of what failed.
    """
    if not isinstance(data, dict):
        return False, f"Expected dict, got {type(data).__name__}"

    # Revenue segments
    segments = data.get("revenue_segments")
    if not isinstance(segments, list) or len(segments) == 0:
        return False, "Missing or empty revenue_segments array"
    has_named = any(
        isinstance(s, dict) and s.get("segment_name")
        for s in segments
    )
    if not has_named:
        return False, "No revenue segment has a segment_name"

    # Gross margin
    gm = data.get("gross_margin")
    if not isinstance(gm, dict):
        return False, "Missing or invalid gross_margin (expected dict)"

    # Free cash flow
    fcf = data.get("free_cash_flow")
    if not isinstance(fcf, dict):
        return False, "Missing or invalid free_cash_flow (expected dict)"

    return True, None


# ── Supply chain extraction validation ───────────────────────────────────────

def validate_supply_chain(data):
    """Validate parsed JSON from supply_chain_extraction prompt.

    Checks each item in the array for:
    - supplier_name (non-empty string)
    - customer_name (non-empty string)
    - relationship_type (one of the four valid types)
    - confidence (float between 0.0 and 1.0)

    Invalid items are filtered out rather than rejecting the whole extraction.
    An empty array [] is a valid response (filing had no supply chain relationships).

    Returns:
        (cleaned_list, skipped_count, skip_reasons)
        cleaned_list: list of valid items.
        skipped_count: number of items that failed validation.
        skip_reasons: list of strings describing why each item was skipped.
    """
    # Empty array is valid — filing had no relationships
    if isinstance(data, list) and len(data) == 0:
        return [], 0, []

    # Non-list input is invalid
    if not isinstance(data, list):
        return [], 1, [f"Expected list, got {type(data).__name__}"]

    cleaned = []
    skipped = 0
    skip_reasons = []

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            skipped += 1
            skip_reasons.append(f"Item {i}: not a dict")
            continue

        # Required string fields
        supplier = item.get("supplier_name")
        customer = item.get("customer_name")
        if not isinstance(supplier, str) or not supplier.strip():
            skipped += 1
            skip_reasons.append(f"Item {i}: missing or empty supplier_name")
            continue
        if not isinstance(customer, str) or not customer.strip():
            skipped += 1
            skip_reasons.append(f"Item {i}: missing or empty customer_name")
            continue

        # Relationship type must be one of the four valid types
        rel_type = item.get("relationship_type")
        if rel_type not in VALID_RELATIONSHIP_TYPES:
            skipped += 1
            skip_reasons.append(
                f"Item {i} ({supplier}→{customer}): "
                f"invalid relationship_type '{rel_type}'"
            )
            continue

        # Confidence must be a number between 0.0 and 1.0
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)):
            skipped += 1
            skip_reasons.append(
                f"Item {i} ({supplier}→{customer}): "
                f"confidence is not a number"
            )
            continue
        if not (0.0 <= confidence <= 1.0):
            skipped += 1
            skip_reasons.append(
                f"Item {i} ({supplier}→{customer}): "
                f"confidence {confidence} out of range [0.0, 1.0]"
            )
            continue

        cleaned.append(item)

    return cleaned, skipped, skip_reasons


# ── Financials row extraction ────────────────────────────────────────────────

def _safe_float(val):
    """Coerce to float or return None."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def extract_financials_row(fundamental_json, company_id, filing):
    """Convert a validated fundamental extraction dict into a row dict
    ready to insert into the financials table.

    Args:
        fundamental_json: validated dict from fundamental_extraction prompt.
        company_id: integer company ID.
        filing: dict with keys from the filings table row
                (ticker, form_type, period_of_report, filed_date).

    Returns:
        dict with keys matching the financials table columns,
        or None if the extraction lacks minimum data to form a row.
    """
    gm = fundamental_json.get("gross_margin", {})
    fcf = fundamental_json.get("free_cash_flow", {})
    roic_data = fundamental_json.get("roic", {})

    # Sum revenue across segments
    segments = fundamental_json.get("revenue_segments", [])
    total_revenue = None
    segment_revenues = [
        _safe_float(s.get("revenue_usd"))
        for s in segments if _safe_float(s.get("revenue_usd")) is not None
    ]
    if segment_revenues:
        total_revenue = sum(segment_revenues)

    gross_margin = _safe_float(gm.get("current_period"))

    # Derive gross_profit from revenue and margin if both available
    gross_profit = None
    if total_revenue is not None and gross_margin is not None:
        gross_profit = total_revenue * gross_margin

    operating_cf = _safe_float(fcf.get("operating_cash_flow_usd"))
    capex_usd = _safe_float(fcf.get("capex_usd"))
    free_cash_flow = _safe_float(fcf.get("fcf_usd"))

    # If FCF not directly stated, compute it
    if free_cash_flow is None and operating_cf is not None and capex_usd is not None:
        free_cash_flow = operating_cf - abs(capex_usd)

    # Determine period_type from form_type
    form_type = filing.get("form_type", "")
    period_type = "annual" if form_type == "10-K" else "quarterly"

    return {
        "company_id": company_id,
        "ticker": filing.get("ticker"),
        "period": filing.get("period_of_report"),
        "period_type": period_type,
        "revenue": total_revenue,
        "gross_profit": gross_profit,
        "gross_margin": gross_margin,
        "operating_income": None,  # not directly extracted by prompt
        "net_income": None,        # not directly extracted by prompt
        "free_cash_flow": free_cash_flow,
        "roic": _safe_float(roic_data.get("value")) if isinstance(roic_data, dict) else None,
        "capex": capex_usd,
        "shares_outstanding": None,  # not directly extracted by prompt
        "filed_date": filing.get("filed_date"),
    }
