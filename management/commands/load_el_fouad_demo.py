"""
Load demo Branch Product Stock data for El Fouad.

Creates sample products (if needed) and sets on_hand quantities
per branch so the sales order form can demonstrate the feature.

Usage:
    python manage.py load_el_fouad_demo
"""
from django.core.management.base import BaseCommand
from decimal import Decimal


DEMO_PRODUCTS = [
    {"name": "Steel Pipes 2\"",     "sale_price": 120.00, "type": "product"},
    {"name": "Steel Pipes 4\"",     "sale_price": 200.00, "type": "product"},
    {"name": "Copper Wire 10mm",    "sale_price": 85.00,  "type": "product"},
    {"name": "Copper Wire 16mm",    "sale_price": 130.00, "type": "product"},
    {"name": "Cement Bags 50kg",    "sale_price": 45.00,  "type": "product"},
    {"name": "Iron Rods 12mm",      "sale_price": 95.00,  "type": "product"},
    {"name": "PVC Pipes 3\"",       "sale_price": 35.00,  "type": "product"},
    {"name": "Paint (White) 20L",   "sale_price": 180.00, "type": "product"},
]

# on_hand per branch index (cycles if fewer branches exist)
# Format: list of values for branch 0, 1, 2, 3 ...
STOCK_MATRIX = {
    "Steel Pipes 2\"":   [120, 85,  200, 60],
    "Steel Pipes 4\"":   [50,  70,  30,  90],
    "Copper Wire 10mm":  [300, 150, 400, 200],
    "Copper Wire 16mm":  [180, 90,  250, 120],
    "Cement Bags 50kg":  [500, 300, 700, 400],
    "Iron Rods 12mm":    [200, 100, 350, 150],
    "PVC Pipes 3\"":     [400, 250, 600, 300],
    "Paint (White) 20L": [80,  60,  120, 40],
}


class Command(BaseCommand):
    help = "Load El Fouad demo products and branch stock quantities"

    def handle(self, *args, **options):
        from modules.products.models import ProductTemplate
        from modules.base.models.branch import Branch
        from el_fouad.models import BranchProductStock

        # ── 1. Get branches ───────────────────────────────────────────────
        branches = list(Branch.objects.all().order_by('id'))
        if not branches:
            self.stderr.write("No branches found. Please create at least one branch first.")
            return
        self.stdout.write(f"Found {len(branches)} branch(es): {', '.join(b.name for b in branches)}")

        # ── 2. Ensure products exist ──────────────────────────────────────
        from modules.base.models.company import Company
        company = Company.objects.first()
        if not company:
            self.stderr.write("No company found. Please create a company first.")
            return

        products = []
        for spec in DEMO_PRODUCTS:
            product, created = ProductTemplate.objects.get_or_create(
                name=spec["name"],
                defaults={
                    "sale_price": Decimal(str(spec["sale_price"])),
                    "type": spec["type"],
                    "company": company,
                },
            )
            action = "created" if created else "exists"
            self.stdout.write(f"  [{action}] {product.name}")
            products.append(product)

        # ── 3. Create / update BranchProductStock ─────────────────────────
        created_count = updated_count = 0
        for branch_idx, branch in enumerate(branches):
            for product in products:
                qty_list = STOCK_MATRIX.get(product.name, [50])
                qty = Decimal(str(qty_list[branch_idx % len(qty_list)]))

                stock, created = BranchProductStock.objects.update_or_create(
                    branch=branch,
                    product=product,
                    defaults={"on_hand": qty},
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone — {created_count} stock records created, {updated_count} updated."
        ))
        self.stdout.write(
            f"Open El Fouad → Branch Stock to review the quantities."
        )
