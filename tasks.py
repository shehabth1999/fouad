# -*- coding: utf-8 -*-
"""
Celery tasks for El Fouad — Alfouad Pharmacies API integration.
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)

CHUNK_SIZE_DEFAULT = 500


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_stock_from_api(self):
    """
    Fetch /api/get_stock from Alfouad and bulk-update BranchProductStock.
    - Skips records with missing required fields (never crashes)
    - Processes in configurable chunks for memory efficiency
    - Updates last_stock_sync timestamp on success
    """
    from el_fouad.models import AlfouadAPIConfig, BranchProductStock
    from el_fouad.services.alfouad_api import get_stock
    from modules.base.models.branch import Branch
    from modules.products.models import ProductTemplate
    from decimal import Decimal

    cfg = AlfouadAPIConfig.objects.filter(enabled=True).first()
    if not cfg:
        logger.info("[Alfouad] sync_stock_from_api: no active config, skipping.")
        return {'status': 'skipped', 'reason': 'no active config'}

    if cfg.stock_sync_status == 'running':
        logger.warning("[Alfouad] sync_stock_from_api: already running, skipping.")
        return {'status': 'skipped', 'reason': 'already running'}

    cfg.mark_sync_start()

    try:
        # ── 1. Fetch from API ────────────────────────────────────────────────
        records, error = get_stock()
        if error:
            cfg.mark_sync_failed(error)
            logger.error("[Alfouad] get_stock failed: %s", error)
            return {'status': 'failed', 'error': error}

        if not records:
            cfg.mark_sync_done()
            return {'status': 'ok', 'total': 0, 'created': 0, 'updated': 0}

        chunk = cfg.chunk_size or CHUNK_SIZE_DEFAULT

        # ── 2. Auto-create + name-update products ────────────────────────────
        api_products: dict = {}  # ref → {'name': ..., 'price': ...}
        for rec in records:
            ref      = rec['product_ref']
            raw_name = rec.get('product_name') or ref
            name     = f"({ref}) {raw_name}"
            price    = Decimal(str(rec.get('product_price') or 0))
            if ref not in api_products or price > api_products[ref]['price']:
                api_products[ref] = {'name': name, 'price': price}

        existing = {
            p.reference: p
            for p in ProductTemplate.objects.exclude(reference__isnull=True)
                                            .exclude(reference='')
                                            .only('id', 'reference', 'name', 'sale_price')
        }
        missing_refs = set(api_products.keys()) - set(existing.keys())

        if missing_refs:
            from modules.base.models.company import Company
            company = Company.objects.first()
            new_products = [
                ProductTemplate(
                    name=api_products[ref]['name'],
                    reference=ref,
                    sale_price=api_products[ref]['price'],
                    company=company,
                    type='product',
                    sale_ok=True,
                )
                for ref in missing_refs
            ]
            for i in range(0, len(new_products), chunk):
                batch = new_products[i:i + chunk]
                ProductTemplate.objects.bulk_create(batch, ignore_conflicts=True)
            logger.info("[Alfouad] Created %d new products", len(missing_refs))

        # Update names that don't yet match the (ref) name format
        to_rename = []
        for ref, info in api_products.items():
            prod = existing.get(ref)
            if prod and prod.name != info['name']:
                prod.name = info['name']
                to_rename.append(prod)
        if to_rename:
            for i in range(0, len(to_rename), chunk):
                batch = to_rename[i:i + chunk]
                ProductTemplate.objects.bulk_update(batch, fields=['name'])
            logger.info("[Alfouad] Updated names for %d products", len(to_rename))

        # ── 3. Build lookup dicts (single DB round-trip each) ────────────────
        branch_map = {
            b.location_code: b.id
            for b in Branch.objects.exclude(location_code__isnull=True)
                                   .exclude(location_code='')
                                   .only('id', 'location_code')
        }
        product_map = {
            p.reference: p.id
            for p in ProductTemplate.objects.exclude(reference__isnull=True)
                                            .exclude(reference='')
                                            .only('id', 'reference')
        }

        # ── 5. Classify records ──────────────────────────────────────────────
        existing_keys = set(
            BranchProductStock.objects.values_list('branch_id', 'product_id')
        )

        to_create, to_update_map = [], {}
        unmatched_branch = unmatched_product = 0

        for rec in records:
            loc_code   = rec['location_code']
            prod_ref   = rec['product_ref']
            qty        = Decimal(str(rec['qty']))
            branch_id  = branch_map.get(loc_code)
            product_id = product_map.get(prod_ref)

            if branch_id is None:
                unmatched_branch += 1
                logger.debug("[Alfouad] no Branch for location_code=%s", loc_code)
                continue
            if product_id is None:
                unmatched_product += 1
                logger.debug("[Alfouad] no Product for product_ref=%s", prod_ref)
                continue

            key = (branch_id, product_id)
            if key in existing_keys:
                to_update_map[key] = qty
            else:
                to_create.append(
                    BranchProductStock(branch_id=branch_id, product_id=product_id, on_hand=qty)
                )

        # ── 6. Bulk CREATE in chunks ─────────────────────────────────────────
        created_count = 0
        for i in range(0, len(to_create), chunk):
            batch = to_create[i:i + chunk]
            BranchProductStock.objects.bulk_create(batch, ignore_conflicts=True)
            created_count += len(batch)

        # ── 7. Bulk UPDATE in chunks ─────────────────────────────────────────
        updated_count = 0
        if to_update_map:
            existing_objs = {
                (s.branch_id, s.product_id): s
                for s in BranchProductStock.objects.filter(
                    branch_id__in={k[0] for k in to_update_map},
                    product_id__in={k[1] for k in to_update_map},
                )
            }
            to_update_list = []
            for key, qty in to_update_map.items():
                obj = existing_objs.get(key)
                if obj:
                    obj.on_hand = qty
                    to_update_list.append(obj)

            for i in range(0, len(to_update_list), chunk):
                batch = to_update_list[i:i + chunk]
                BranchProductStock.objects.bulk_update(batch, fields=['on_hand'])
                updated_count += len(batch)

        cfg.mark_sync_done()

        result = {
            'status':            'ok',
            'total_api':         len(records),
            'created':           created_count,
            'updated':           updated_count,
            'unmatched_branch':  unmatched_branch,
            'unmatched_product': unmatched_product,
        }
        logger.info("[Alfouad] stock sync complete: %s", result)
        return result

    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        cfg.mark_sync_failed(msg)
        logger.exception("[Alfouad] sync_stock_from_api unexpected error: %s", exc)
        raise self.retry(exc=exc)
