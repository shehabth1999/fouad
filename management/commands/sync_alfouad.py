"""
El Fouad — Alfouad Pharmacies synchronization command.

Usage
-----
  # First-time setup: clean test data, create branches, seed config, initial sync
  python manage.py sync_alfouad --init

  # Sync stock only (same logic as the Celery task)
  python manage.py sync_alfouad --stock

  # Sync locations / branches only
  python manage.py sync_alfouad --locations

  # Default (no flag): --stock
  python manage.py sync_alfouad
"""
import logging
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

# Demo products created by load_el_fouad_demo — safe to delete
_DEMO_PRODUCT_NAMES = [
    'Steel Pipes 2"', 'Steel Pipes 4"',
    'Copper Wire 10mm', 'Copper Wire 16mm',
    'Cement Bags 50kg', 'Iron Rods 12mm',
    'PVC Pipes 3"', 'Paint (White) 20L',
]

CHUNK = 500


class Command(BaseCommand):
    help = "Synchronize branches and stock from Alfouad Pharmacies API"

    def add_arguments(self, parser):
        parser.add_argument('--init',      action='store_true',
                            help='Clean test data, create branches, seed config, sync stock')
        parser.add_argument('--stock',     action='store_true', help='Sync stock only')
        parser.add_argument('--locations', action='store_true', help='Sync branches only')

    # ── entry point ────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        do_init      = options['init']
        do_stock     = options['stock']
        do_locations = options['locations']

        if not any([do_init, do_stock, do_locations]):
            do_stock = True  # default

        if do_init:
            self._clean_test_data()
            self._seed_config()
            self._sync_locations()
            self._sync_stock()
        else:
            if do_locations:
                self._sync_locations()
            if do_stock:
                self._sync_stock()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _seed_config(self):
        """Create AlfouadAPIConfig if it doesn't exist."""
        from el_fouad.models import AlfouadAPIConfig
        obj, created = AlfouadAPIConfig.objects.get_or_create(
            base_url='https://alfouadpharmacies.roayatec.com',
            defaults={
                'api_key':  'DMR2as5Sh5kNUPhE9mgGiOa1fqqJsmMaqG8IaUO',
                'enabled':  True,
                'chunk_size': 500,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("  Created AlfouadAPIConfig"))
        else:
            self.stdout.write("  AlfouadAPIConfig already exists")

    # ── cleanup ─────────────────────────────────────────────────────────────

    def _clean_test_data(self):
        self.stdout.write("\n── Cleaning test data ──")

        # Remove test branches (not headquarters)
        from modules.base.models.branch import Branch
        test_branches = Branch.objects.filter(is_headquarters=False)
        count = test_branches.count()
        if count:
            test_branches.delete()
            self.stdout.write(self.style.WARNING(
                f"  Deleted {count} non-HQ test branch(es)"
            ))
        else:
            self.stdout.write("  No test branches to delete")

        # Remove demo products — cascade through order lines first
        from modules.products.models import ProductTemplate
        from modules.sales.models.order import SalesOrderLine, SalesOrder

        demo = ProductTemplate.objects.filter(name__in=_DEMO_PRODUCT_NAMES)
        demo_ids = list(demo.values_list('id', flat=True))
        if demo_ids:
            # Delete order lines referencing these products
            lines_qs = SalesOrderLine.objects.filter(product_id__in=demo_ids)
            line_count = lines_qs.count()
            if line_count:
                # Collect affected orders before deleting lines
                order_ids = list(lines_qs.values_list('order_id_id', flat=True).distinct())
                lines_qs.delete()
                self.stdout.write(self.style.WARNING(
                    f"  Deleted {line_count} order line(s) for demo products"
                ))
                # Remove now-empty orders
                empty_orders = SalesOrder.objects.filter(
                    id__in=order_ids
                ).annotate(
                    line_cnt=models.Count('order_lines')
                ).filter(line_cnt=0)
                eo_count = empty_orders.count()
                if eo_count:
                    empty_orders.delete()
                    self.stdout.write(self.style.WARNING(
                        f"  Deleted {eo_count} now-empty order(s)"
                    ))

            prod_count = demo.count()
            demo.delete()
            self.stdout.write(self.style.WARNING(
                f"  Deleted {prod_count} demo product(s)"
            ))
        else:
            self.stdout.write("  No demo products to delete")

    # ── locations / branches ─────────────────────────────────────────────

    def _sync_locations(self):
        self.stdout.write("\n── Syncing locations (branches) ──")
        from el_fouad.services.alfouad_api import get_locations
        from modules.base.models.branch import Branch
        from modules.base.models.company import Company

        locations, error = get_locations()
        if error:
            self.stderr.write(self.style.ERROR(f"  API error: {error}"))
            return

        # Headquarters company
        company = Company.objects.first()
        if not company:
            self.stderr.write(self.style.ERROR("  No company found. Create one first."))
            return

        self.stdout.write(f"  Using company: {company.name}")
        self.stdout.write(f"  {len(locations)} locations from API")

        existing_codes = set(
            Branch.objects.exclude(location_code__isnull=True)
                          .values_list('location_code', flat=True)
        )

        to_create = []
        updated = created = 0

        for loc in locations:
            code = loc['location_code']
            name = loc['warehouse_name']

            if code in existing_codes:
                # Update name if changed
                Branch.objects.filter(location_code=code).update(name=name)
                updated += 1
            else:
                to_create.append(Branch(
                    company=company,
                    name=name,
                    code=code,
                    location_code=code,
                    is_headquarters=False,
                ))

        # Bulk create in chunks
        for i in range(0, len(to_create), CHUNK):
            batch = to_create[i:i + CHUNK]
            Branch.objects.bulk_create(batch, ignore_conflicts=True)
            created += len(batch)

        self.stdout.write(self.style.SUCCESS(
            f"  Branches created: {created}  updated: {updated}"
        ))

        # Sync timestamp
        from el_fouad.models import AlfouadAPIConfig
        AlfouadAPIConfig.objects.filter(enabled=True).update(
            last_locations_sync=timezone.now()
        )

    # ── stock ─────────────────────────────────────────────────────────────

    def _sync_stock(self):
        self.stdout.write("\n── Syncing stock ──")
        from el_fouad.services.alfouad_api import get_stock
        from el_fouad.models import AlfouadAPIConfig, BranchProductStock
        from modules.base.models.branch import Branch
        from modules.products.models import ProductTemplate

        cfg = AlfouadAPIConfig.objects.filter(enabled=True).first()
        if not cfg:
            self.stderr.write(self.style.ERROR("  No active AlfouadAPIConfig. Run --init first."))
            return

        records, error = get_stock()
        if error:
            self.stderr.write(self.style.ERROR(f"  API error: {error}"))
            cfg.mark_sync_failed(error)
            return

        self.stdout.write(f"  {len(records)} stock records from API")

        chunk = cfg.chunk_size or CHUNK

        # ── Auto-create + update products ─────────────────────────────────
        # Build a map: ref → {name, price}  (keep highest price per ref)
        api_products: dict = {}  # ref → {'name': ..., 'price': ...}
        for rec in records:
            ref        = rec['product_ref']
            raw_name   = rec.get('product_name') or ref
            name       = f"({ref}) {raw_name}"
            price      = Decimal(str(rec.get('product_price') or 0))
            if ref not in api_products or price > api_products[ref]['price']:
                api_products[ref] = {'name': name, 'price': price}

        existing = {
            p.reference: p
            for p in ProductTemplate.objects.exclude(reference__isnull=True)
                                            .exclude(reference='')
                                            .only('id', 'reference', 'name', 'sale_price')
        }

        missing_refs = set(api_products.keys()) - set(existing.keys())

        # Create new products
        if missing_refs:
            from modules.base.models.company import Company
            from modules.products.models import Uom
            company  = Company.objects.first()
            # Use the first available UOM — prevents FK violation when uom_id=1 absent
            default_uom = Uom.objects.first()
            new_products = [
                ProductTemplate(
                    name=api_products[ref]['name'],
                    reference=ref,
                    sale_price=api_products[ref]['price'],
                    company=company,
                    type='product',
                    sale_ok=True,
                    **({'uom': default_uom, 'uom_po': default_uom} if default_uom else {}),
                )
                for ref in missing_refs
            ]
            products_created = 0
            for i in range(0, len(new_products), chunk):
                batch = new_products[i:i + chunk]
                ProductTemplate.objects.bulk_create(batch, ignore_conflicts=True)
                products_created += len(batch)
            self.stdout.write(self.style.SUCCESS(
                f"  Products created: {products_created}"
            ))

        # Update names on EXISTING products whose name doesn't match the (ref) name format
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
            self.stdout.write(self.style.SUCCESS(
                f"  Product names updated: {len(to_rename)}"
            ))

        # Lookup dicts — one DB query each
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

        existing_keys = set(
            BranchProductStock.objects.values_list('branch_id', 'product_id')
        )

        to_create, to_update_map = [], {}
        unmatched_b = unmatched_p = 0

        for rec in records:
            branch_id  = branch_map.get(rec['location_code'])
            product_id = product_map.get(rec['product_ref'])
            qty        = Decimal(str(rec['qty']))

            if branch_id is None:
                unmatched_b += 1
                continue
            if product_id is None:
                unmatched_p += 1
                continue

            key = (branch_id, product_id)
            if key in existing_keys:
                to_update_map[key] = qty
            else:
                to_create.append(
                    BranchProductStock(branch_id=branch_id, product_id=product_id, on_hand=qty)
                )

        # Bulk CREATE
        chunk = cfg.chunk_size or CHUNK
        created_n = 0
        for i in range(0, len(to_create), chunk):
            batch = to_create[i:i + chunk]
            BranchProductStock.objects.bulk_create(batch, ignore_conflicts=True)
            created_n += len(batch)

        # Bulk UPDATE
        updated_n = 0
        if to_update_map:
            objs = {
                (s.branch_id, s.product_id): s
                for s in BranchProductStock.objects.filter(
                    branch_id__in={k[0] for k in to_update_map},
                    product_id__in={k[1] for k in to_update_map},
                )
            }
            dirty = []
            for key, qty in to_update_map.items():
                obj = objs.get(key)
                if obj:
                    obj.on_hand = qty
                    dirty.append(obj)

            for i in range(0, len(dirty), chunk):
                batch = dirty[i:i + chunk]
                BranchProductStock.objects.bulk_update(batch, fields=['on_hand'])
                updated_n += len(batch)

        cfg.mark_sync_done()

        self.stdout.write(self.style.SUCCESS(
            f"  Created: {created_n}  Updated: {updated_n}  "
            f"Unmatched-branch: {unmatched_b}  Unmatched-product: {unmatched_p}"
        ))
