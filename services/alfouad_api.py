# -*- coding: utf-8 -*-
"""
Alfouad Pharmacies API client.

All methods validate required fields before returning data.
If a record is missing a required field it is SKIPPED and logged as a warning —
the caller receives only clean, complete records.
"""
import logging
import re
import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

REQUIRED_STOCK_FIELDS    = ('location_code', 'product_ref', 'product_name', 'qty')
REQUIRED_LOCATION_FIELDS = ('location_code', 'warehouse_name')
REQUIRED_ORDER_FIELDS    = ('success', 'sale_order_id', 'name')

TIMEOUT = 15  # seconds per HTTP call


def _get_config():
    """Return the active AlfouadAPIConfig, or None."""
    try:
        from el_fouad.models import AlfouadAPIConfig
        return AlfouadAPIConfig.objects.filter(enabled=True).first()
    except Exception as exc:
        logger.error("Could not load AlfouadAPIConfig: %s", exc)
        return None


def _headers(api_key):
    return {'Api-Key': api_key, 'Content-Type': 'application/json'}


def _validate(records, required_fields, endpoint):
    """Return (clean_records, skipped_count)."""
    clean, skipped = [], 0
    for rec in records:
        missing = [f for f in required_fields if rec.get(f) in (None, '', [])]
        if missing:
            logger.warning(
                "[Alfouad] %s — skipping record with missing fields %s: %s",
                endpoint, missing, rec,
            )
            skipped += 1
        else:
            clean.append(rec)
    return clean, skipped


def _clean_branch_name(warehouse_name: str) -> str:
    """
    Extract the simple branch name from the Alfouad warehouse name.

    Examples:
        'Alfouad1 (batal)'            → 'Batal'
        'Alfouad7 (hadayiq alahram)'  → 'Hadayiq Alahram'
        'Alfouad17  (dagla jardinz)'  → 'Dagla Jardinz'
        'Alfouad6 (altajamue )'       → 'Altajamue'
    """
    match = re.search(r'\(([^)]+)\)', warehouse_name)
    if match:
        return match.group(1).strip().title()
    # Fallback: strip the "AlfouadN " prefix
    fallback = re.sub(r'^Alfouad\d*\s*', '', warehouse_name, flags=re.IGNORECASE).strip().title()
    return fallback or warehouse_name


# ── Public API ──────────────────────────────────────────────────────────────

def get_locations():
    """
    Fetch /api/locations.
    Returns (list[dict], error_message_or_None).
    Each dict guaranteed to have: location_code, warehouse_name.
    """
    cfg = _get_config()
    if not cfg:
        return [], "No active AlfouadAPIConfig found."

    url = f"{cfg.base_url.rstrip('/')}/api/locations"
    try:
        resp = requests.get(url, headers=_headers(cfg.api_key), timeout=TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
    except requests.Timeout:
        return [], f"GET {url} timed out after {TIMEOUT}s"
    except requests.HTTPError as exc:
        return [], f"HTTP {exc.response.status_code} from {url}: {exc.response.text[:200]}"
    except Exception as exc:
        return [], f"GET {url} failed: {exc}"

    if not isinstance(raw, list):
        return [], f"/api/locations returned non-list: {type(raw)}"

    clean, skipped = _validate(raw, REQUIRED_LOCATION_FIELDS, '/api/locations')
    if skipped:
        logger.warning("[Alfouad] locations: %d records skipped (missing fields)", skipped)

    # Normalise warehouse names: "Alfouad7 (hadayiq alahram)" → "Alfouad Hadayiq Alahram"
    for rec in clean:
        rec['warehouse_name'] = _clean_branch_name(rec['warehouse_name'])

    return clean, None


def get_stock():
    """
    Fetch /api/get_stock.
    Returns (list[dict], error_message_or_None).
    Each dict guaranteed to have: location_code, product_ref, qty.
    """
    cfg = _get_config()
    if not cfg:
        return [], "No active AlfouadAPIConfig found."

    url = f"{cfg.base_url.rstrip('/')}/api/get_stock_named"
    try:
        resp = requests.get(url, headers=_headers(cfg.api_key), timeout=TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
    except requests.Timeout:
        return [], f"GET {url} timed out after {TIMEOUT}s"
    except requests.HTTPError as exc:
        return [], f"HTTP {exc.response.status_code} from {url}: {exc.response.text[:200]}"
    except Exception as exc:
        return [], f"GET {url} failed: {exc}"

    if not isinstance(raw, list):
        return [], f"/api/get_stock returned non-list: {type(raw)}"

    clean, skipped = _validate(raw, REQUIRED_STOCK_FIELDS, '/api/get_stock')
    if skipped:
        logger.warning("[Alfouad] get_stock: %d records skipped (missing fields)", skipped)
    return clean, None


def create_sale_order(payload: dict):
    """
    POST /api/create_sale_order.
    Returns (response_dict_or_None, error_message_or_None).
    response_dict is guaranteed to have: success, sale_order_id, name.
    """
    cfg = _get_config()
    if not cfg:
        return None, "No active AlfouadAPIConfig found."

    url = f"{cfg.base_url.rstrip('/')}/api/create_sale_order"
    try:
        resp = requests.post(url, json=payload, headers=_headers(cfg.api_key), timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        return None, f"POST {url} timed out after {TIMEOUT}s"
    except requests.HTTPError as exc:
        return None, f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
    except Exception as exc:
        return None, f"POST {url} failed: {exc}"

    if not data.get('success'):
        return None, data.get('error', 'API returned success=false with no message')

    clean, skipped = _validate([data], REQUIRED_ORDER_FIELDS, '/api/create_sale_order')
    if skipped:
        return None, "API response missing required fields (sale_order_id / name)"

    return data, None
