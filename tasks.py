# -*- coding: utf-8 -*-
"""
Celery tasks for El Fouad — Alfouad Pharmacies API integration.
"""
import gc
import json
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

CHUNK_SIZE_DEFAULT = 500

# A 'running' flag older than this is treated as abandoned and taken over.
# The task itself finishes in a couple of minutes; anything past this window
# means the worker holding the lock died without releasing it (OOM kill,
# --max-memory-per-child recycle, service restart), which used to wedge the
# sync permanently because nothing ever reset the flag.
STALE_LOCK_MINUTES = 30

# The stock feed is ~21 MB; the default 15 s client timeout is too tight.
FETCH_TIMEOUT = 180

# Rows are streamed out of the DB in slices this size while building the
# in-memory index, so the whole table is never materialised as model objects.
DB_ITER_CHUNK = 5000


def _chunks(seq, size):
    """Yield successive slices of `seq` of at most `size` items."""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _lock_is_stale(cfg):
    """True when a 'running' flag is old enough to be considered abandoned."""
    stamp = cfg.updated_at
    if not stamp:
        return True
    return (timezone.now() - stamp) > timedelta(minutes=STALE_LOCK_MINUTES)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_stock_from_api(self):
    """
    Fetch the Alfouad stock feed and reconcile BranchProductStock against it.

    Reconciliation is full, not incremental:
      - pair in feed, no row      → create
      - pair in feed, row differs → update
      - row with no pair in feed  → zeroed (Odoo no longer stocks it)

    Every write phase runs in chunks, and the feed is compacted to plain
    tuples before any DB work starts, so peak memory stays well under the
    worker's --max-memory-per-child ceiling.
    """
    from decimal import Decimal

    from el_fouad.models import AlfouadAPIConfig, BranchProductStock
    # Fetched inline rather than via services.get_stock(): that helper has a
    # 15 s timeout shared with order posting, and it materialises a second full
    # copy of the ~21 MB feed that this task would immediately discard.
    from el_fouad.services.alfouad_api import _headers
    from modules.base.models.branch import Branch
    from modules.products.models import ProductTemplate
    import requests

    cfg = AlfouadAPIConfig.objects.filter(enabled=True).first()
    if not cfg:
        logger.info("[Alfouad] sync_stock_from_api: no active config, skipping.")
        return {'status': 'skipped', 'reason': 'no active config'}

    if cfg.stock_sync_status == 'running':
        if not _lock_is_stale(cfg):
            logger.warning("[Alfouad] sync_stock_from_api: already running, skipping.")
            return {'status': 'skipped', 'reason': 'already running'}
        logger.error(
            "[Alfouad] stale 'running' lock from %s — taking over.", cfg.updated_at,
        )

    cfg.mark_sync_start()
    started = timezone.now()

    try:
        # ── 1. Fetch ────────────────────────────────────────────────────────
        url = f"{cfg.base_url.rstrip('/')}/api/get_stock_named"
        resp = requests.get(url, headers=_headers(cfg.api_key), timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        raw = json.loads(resp.content)
        del resp
        if not isinstance(raw, list):
            raise ValueError(f"{url} returned {type(raw).__name__}, expected list")
        if not raw:
            cfg.mark_sync_done()
            return {'status': 'ok', 'total_api': 0}

        logger.info("[Alfouad] fetched %d stock records", len(raw))

        # ── 2. Compact the feed, then drop the parsed JSON ──────────────────
        # feed: (location_code, product_ref) -> qty
        # api_products: product_ref -> (display_name, price)
        feed = {}
        api_products = {}
        malformed = 0
        for rec in raw:
            loc = rec.get('location_code')
            ref = rec.get('product_ref')
            qty = rec.get('qty')
            if not loc or not ref or qty is None:
                malformed += 1
                continue
            try:
                feed[(loc, ref)] = Decimal(str(qty))
            except Exception:
                malformed += 1
                continue
            price = Decimal(str(rec.get('product_price') or 0))
            name = f"({ref}) {rec.get('product_name') or ref}"
            known = api_products.get(ref)
            if known is None or price > known[1]:
                api_products[ref] = (name, price)

        del raw
        gc.collect()
        logger.info(
            "[Alfouad] compacted to %d pairs / %d products (%d malformed skipped)",
            len(feed), len(api_products), malformed,
        )

        chunk = cfg.chunk_size or CHUNK_SIZE_DEFAULT

        # ── 3. Create products the feed knows about and we don't ────────────
        existing_refs = dict(
            ProductTemplate.objects.exclude(reference__isnull=True)
                                   .exclude(reference='')
                                   .values_list('reference', 'id')
        )
        missing_refs = [r for r in api_products if r not in existing_refs]
        products_created = 0
        if missing_refs:
            from modules.base.models.company import Company
            from modules.products.models import Uom
            company = Company.objects.first()
            default_uom = Uom.objects.first()
            uom_kw = {'uom': default_uom, 'uom_po': default_uom} if default_uom else {}
            for batch in _chunks(missing_refs, chunk):
                ProductTemplate.objects.bulk_create(
                    [
                        ProductTemplate(
                            name=api_products[ref][0],
                            reference=ref,
                            sale_price=api_products[ref][1],
                            company=company,
                            type='product',
                            sale_ok=True,
                            **uom_kw,
                        )
                        for ref in batch
                    ],
                    ignore_conflicts=True,
                )
                products_created += len(batch)
                logger.info("[Alfouad] products created: %d/%d",
                            products_created, len(missing_refs))

        # ── 4. Refresh names AND prices that drifted ────────────────────────
        # Odoo owns both. sale_price used to be written only at creation, so
        # products kept whatever price they had on the day they first appeared
        # — and order lines auto-fill price_unit from sale_price, so a stale
        # value is quoted to the customer and then posted back to Odoo.
        renamed = repriced = 0
        price_guarded = 0
        drifted = []
        for ref, pid, cur_name, cur_price in (
            ProductTemplate.objects.exclude(reference__isnull=True)
                                   .exclude(reference='')
                                   .values_list('reference', 'id', 'name', 'sale_price')
                                   .iterator(chunk_size=DB_ITER_CHUNK)
        ):
            want = api_products.get(ref)
            if not want:
                continue
            want_name, want_price = want

            name_off = cur_name != want_name
            current = Decimal(str(cur_price)) if cur_price is not None else None

            # Never let a 0 from the feed wipe a real selling price: the order
            # line would auto-fill 0 and the goods would go out free. Keep the
            # existing price and count it instead.
            if want_price == 0 and current not in (None, Decimal('0')):
                price_guarded += 1
                want_price = current

            price_off = current != want_price
            if name_off or price_off:
                drifted.append((pid, want_name, want_price))
                renamed += 1 if name_off else 0
                repriced += 1 if price_off else 0

        for batch in _chunks(drifted, chunk):
            ProductTemplate.objects.bulk_update(
                [
                    ProductTemplate(id=pid, name=nm, sale_price=pr)
                    for pid, nm, pr in batch
                ],
                fields=['name', 'sale_price'],
            )
        if drifted:
            logger.info(
                "[Alfouad] products refreshed: %d rows (%d renamed, %d repriced, "
                "%d zero-price overwrites blocked)",
                len(drifted), renamed, repriced, price_guarded,
            )

        del api_products, missing_refs, drifted

        # ── 5. Resolve feed keys to DB ids ──────────────────────────────────
        branch_map = dict(
            Branch.objects.exclude(location_code__isnull=True)
                          .exclude(location_code='')
                          .values_list('location_code', 'id')
        )
        product_map = dict(
            ProductTemplate.objects.exclude(reference__isnull=True)
                                   .exclude(reference='')
                                   .values_list('reference', 'id')
        )

        wanted = {}          # (branch_id, product_id) -> qty
        unmatched_branch = unmatched_product = 0
        for (loc, ref), qty in feed.items():
            bid = branch_map.get(loc)
            if bid is None:
                unmatched_branch += 1
                continue
            pid = product_map.get(ref)
            if pid is None:
                unmatched_product += 1
                continue
            wanted[(bid, pid)] = qty

        del feed, branch_map, product_map
        gc.collect()

        # ── 6. Index current rows without loading model objects ─────────────
        current = {}         # (branch_id, product_id) -> (row_id, on_hand)
        for row_id, bid, pid, on_hand in (
            BranchProductStock.objects
            .values_list('id', 'branch_id', 'product_id', 'on_hand')
            .iterator(chunk_size=DB_ITER_CHUNK)
        ):
            current[(bid, pid)] = (row_id, on_hand)

        logger.info("[Alfouad] feed pairs=%d  existing rows=%d", len(wanted), len(current))

        # ── 7. Diff ─────────────────────────────────────────────────────────
        to_create, to_update = [], []
        for key, qty in wanted.items():
            row = current.get(key)
            if row is None:
                to_create.append((key[0], key[1], qty))
            elif row[1] != qty:
                to_update.append((row[0], qty))

        # Rows the feed no longer reports: Odoo does not stock that product at
        # that branch any more, so the honest reading is zero, not "whatever it
        # was last time we heard about it".
        to_zero = [
            (row_id, on_hand)
            for key, (row_id, on_hand) in current.items()
            if key not in wanted and on_hand != 0
        ]

        del wanted, current
        gc.collect()

        logger.info("[Alfouad] plan: create=%d update=%d zero=%d",
                    len(to_create), len(to_update), len(to_zero))

        # ── 8. Apply, in chunks ─────────────────────────────────────────────
        now = timezone.now()

        created_count = 0
        for batch in _chunks(to_create, chunk):
            BranchProductStock.objects.bulk_create(
                [
                    BranchProductStock(branch_id=b, product_id=p, on_hand=q)
                    for b, p, q in batch
                ],
                ignore_conflicts=True,
            )
            created_count += len(batch)
            logger.info("[Alfouad] created %d/%d", created_count, len(to_create))

        # bulk_update bypasses auto_now, so updated_at is stamped explicitly —
        # without it there is no way to tell how old any figure is.
        updated_count = 0
        for batch in _chunks(to_update, chunk):
            BranchProductStock.objects.bulk_update(
                [BranchProductStock(id=i, on_hand=q, updated_at=now) for i, q in batch],
                fields=['on_hand', 'updated_at'],
            )
            updated_count += len(batch)
            logger.info("[Alfouad] updated %d/%d", updated_count, len(to_update))

        zeroed_count = 0
        zero = Decimal('0.000')
        for batch in _chunks(to_zero, chunk):
            BranchProductStock.objects.bulk_update(
                [BranchProductStock(id=i, on_hand=zero, updated_at=now) for i, _ in batch],
                fields=['on_hand', 'updated_at'],
            )
            zeroed_count += len(batch)
            logger.info("[Alfouad] zeroed %d/%d", zeroed_count, len(to_zero))

        cfg.mark_sync_done()

        result = {
            'status':            'ok',
            'seconds':           round((timezone.now() - started).total_seconds(), 1),
            'products_created':  products_created,
            'products_renamed':  renamed,
            'products_repriced': repriced,
            'zero_price_blocked': price_guarded,
            'created':           created_count,
            'updated':           updated_count,
            'zeroed':            zeroed_count,
            'unmatched_branch':  unmatched_branch,
            'unmatched_product': unmatched_product,
            'malformed':         malformed,
        }
        logger.info("[Alfouad] stock sync complete: %s", result)
        return result

    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        cfg.mark_sync_failed(msg)
        logger.exception("[Alfouad] sync_stock_from_api unexpected error: %s", exc)
        raise self.retry(exc=exc)

    finally:
        # Belt and braces: if control left the block without either terminal
        # marker running, do not leave the lock held. A SIGKILL still skips
        # this — that case is covered by the stale-lock takeover above.
        AlfouadAPIConfig.objects.filter(
            pk=cfg.pk, stock_sync_status='running',
        ).update(
            stock_sync_status='failed',
            last_error='sync interrupted before completion',
            updated_at=timezone.now(),
        )
