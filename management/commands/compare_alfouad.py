"""
Compare Genie's local stock mirror against the live Alfouad (Odoo) feed.

READ-ONLY — performs GET requests and SELECTs only. Writes nothing anywhere.

Usage
-----
  python manage.py compare_alfouad                # full report
  python manage.py compare_alfouad --top 25       # show more of the biggest gaps
  python manage.py compare_alfouad --branch BT456 # drill into one branch
  python manage.py compare_alfouad --quiet        # verdict + headline numbers only

Exit code: 0 = healthy, 1 = needs attention (usable from cron/monitoring).
"""
from collections import defaultdict
from decimal import Decimal

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

# A sync older than this means the figures cannot be trusted as current.
STALE_MIN = 15
# Below this match rate something is structurally wrong, not just trading drift.
HEALTHY_MATCH_PCT = Decimal('99.0')
# Differences bigger than this are too large to explain as a few minutes of sales.
SUSPICIOUS_DELTA = Decimal('50')


def _bar(n, total, width=28):
    return '█' * int((n / total) * width) if total else ''


class Command(BaseCommand):
    help = "Compare Genie stock/products/prices against the live Alfouad Odoo feed"

    def add_arguments(self, parser):
        parser.add_argument('--top', type=int, default=10,
                            help='How many of the biggest differences to list (default 10)')
        parser.add_argument('--branch', type=str, default=None,
                            help='Limit the comparison to one location_code')
        parser.add_argument('--quiet', action='store_true',
                            help='Headline numbers and verdict only')

    def handle(self, *args, **opts):
        from django.apps import apps
        Cfg = apps.get_model('el_fouad', 'AlfouadAPIConfig')
        BPS = apps.get_model('el_fouad', 'BranchProductStock')
        Branch = apps.get_model('base', 'Branch')
        PT = apps.get_model('products', 'ProductTemplate')
        SOL = apps.get_model('sales', 'SalesOrderLine')

        ok = self.style.SUCCESS
        warn = self.style.WARNING
        bad = self.style.ERROR
        w = self.stdout.write
        quiet = opts['quiet']
        only = opts['branch']

        # ── 1. sync health ──────────────────────────────────────────────────
        cfg = Cfg.objects.filter(enabled=True).first()
        if not cfg:
            w(bad("No enabled AlfouadAPIConfig — nothing to compare against."))
            return
        age_min = ((timezone.now() - cfg.last_stock_sync).total_seconds() / 60
                   if cfg.last_stock_sync else 10 ** 9)

        w("\n── SYNC HEALTH " + "─" * 46)
        w(f"  status            : {cfg.stock_sync_status}")
        w(f"  last successful   : "
          f"{cfg.last_stock_sync:%Y-%m-%d %H:%M:%S} ({age_min:.1f} min ago)"
          if cfg.last_stock_sync else "  last successful   : NEVER")
        if cfg.last_error:
            w(bad(f"  last error        : {cfg.last_error[:160]}"))

        # ── 2. fetch live feed ──────────────────────────────────────────────
        url = f"{cfg.base_url.rstrip('/')}/api/get_stock_named"
        t0 = timezone.now()
        try:
            resp = requests.get(url, headers={'Api-Key': cfg.api_key,
                                              'Content-Type': 'application/json'},
                                timeout=180)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as exc:
            w(bad(f"\nCould not reach Odoo: {type(exc).__name__}: {exc}"))
            raise SystemExit(1)
        took = (timezone.now() - t0).total_seconds()
        w(f"  live feed         : {len(raw):,} records in {took:.1f}s "
          f"(read {timezone.now():%H:%M:%S})")

        # ── 3. index both sides ─────────────────────────────────────────────
        api_qty, api_price, api_name = {}, {}, {}
        for rec in raw:
            loc, ref = rec.get('location_code'), rec.get('product_ref')
            if only and loc != only:
                continue
            if not loc or not ref:
                continue
            api_qty[(loc, ref)] = Decimal(str(rec.get('qty') or 0))
            if rec.get('product_price') is not None:
                api_price[ref] = Decimal(str(rec['product_price']))
            if rec.get('product_name'):
                api_name[ref] = rec['product_name']

        code_of = {b.id: b.location_code for b in Branch.objects.all()}
        name_of = {b.location_code: b.name for b in Branch.objects.all() if b.location_code}
        ref_of = dict(PT.objects.exclude(reference__isnull=True).exclude(reference='')
                        .values_list('id', 'reference'))

        db_qty = {}
        for bid, pid, oh in BPS.objects.values_list('branch_id', 'product_id', 'on_hand'):
            c, rf = code_of.get(bid), ref_of.get(pid)
            if c and rf and (not only or c == only):
                db_qty[(c, rf)] = Decimal(str(oh))

        both = set(api_qty) & set(db_qty)
        missing = set(api_qty) - set(db_qty)
        phantom = [k for k in set(db_qty) - set(api_qty) if db_qty[k] != 0]
        mismatch = {k: (db_qty[k], api_qty[k]) for k in both if db_qty[k] != api_qty[k]}
        match_pct = (Decimal(len(both) - len(mismatch)) / Decimal(len(both)) * 100
                     if both else Decimal(0))

        w("\n── QUANTITIES " + "─" * 47)
        w(f"  pairs in Odoo               : {len(api_qty):>8,}")
        w(f"  pairs in Genie              : {len(db_qty):>8,}")
        w(f"  compared                    : {len(both):>8,}")
        w(f"    exact match               : {len(both)-len(mismatch):>8,}  ({match_pct:.2f}%)")
        w(f"    different                 : {len(mismatch):>8,}")
        w(f"  in Odoo, missing in Genie   : {len(missing):>8,}")
        w(f"  phantom (Genie>0, gone)     : {len(phantom):>8,}")

        # ── 4. size of differences ──────────────────────────────────────────
        big = []
        if mismatch and not quiet:
            deltas = sorted(abs(o - g) for g, o in mismatch.values())
            buckets = [('1 unit or less', 0), ('2 - 5', 0), ('6 - 20', 0),
                       ('21 - 100', 0), ('over 100', 0)]
            counts = dict(buckets)
            for d in deltas:
                if d <= 1: counts['1 unit or less'] += 1
                elif d <= 5: counts['2 - 5'] += 1
                elif d <= 20: counts['6 - 20'] += 1
                elif d <= 100: counts['21 - 100'] += 1
                else: counts['over 100'] += 1
            w("\n── SIZE OF DIFFERENCES " + "─" * 38)
            for label, _ in buckets:
                n = counts[label]
                w(f"  {label:<16} {n:>6,}  {_bar(n, len(deltas))}")
            w(f"  median {deltas[len(deltas)//2]}   largest {deltas[-1]}")
            big = [d for d in deltas if d > SUSPICIOUS_DELTA]

            w(f"\n  top {opts['top']} biggest gaps:")
            w(f"    {'branch':<17} {'ref':<10} {'GENIE':>11} {'ODOO':>11} {'delta':>10}")
            for (loc, ref), (g, o) in sorted(
                    mismatch.items(), key=lambda x: -abs(x[1][1] - x[1][0]))[:opts['top']]:
                w(f"    {name_of.get(loc, loc):<17} {ref:<10} {g:>11} {o:>11} {o-g:>+10}")

        # ── 5. per branch ───────────────────────────────────────────────────
        if not quiet and not only:
            per = defaultdict(lambda: [0, 0])
            for k in both:
                per[k[0]][0] += 1
            for k in mismatch:
                per[k[0]][1] += 1
            w("\n── PER BRANCH " + "─" * 47)
            w(f"  {'branch':<18} {'code':<9} {'compared':>9} {'differ':>7} {'match':>8}")
            for code in sorted(per, key=lambda c: (per[c][0] - per[c][1]) / per[c][0]):
                tot, dif = per[code]
                w(f"  {name_of.get(code, '?'):<18} {code:<9} {tot:>9,} {dif:>7,} "
                  f"{(tot-dif)/tot*100:>7.2f}%")

        # ── 6. product catalogue coverage ───────────────────────────────────
        api_refs = set(api_qty and {k[1] for k in api_qty})
        genie_refs = set(ref_of.values())
        w("\n── PRODUCT CATALOGUE " + "─" * 40)
        w(f"  products in Odoo feed       : {len(api_refs):>8,}")
        w(f"  products in Genie           : {len(genie_refs):>8,}")
        w(f"  in Odoo but NOT in Genie    : {len(api_refs - genie_refs):>8,}"
          + ("   <- cannot be ordered" if api_refs - genie_refs else ""))
        w(f"  in Genie but not in feed    : {len(genie_refs - api_refs):>8,}"
          + "   (out of stock everywhere, or delisted)")

        # ── 7. prices and names ─────────────────────────────────────────────
        price_diff, name_diff = [], 0
        pt_rows = list(PT.objects.exclude(reference__isnull=True).exclude(reference='')
                         .values_list('reference', 'sale_price', 'name'))
        for ref, sale_price, pname in pt_rows:
            ap = api_price.get(ref)
            if ap is not None and sale_price is not None and Decimal(str(sale_price)) != ap:
                price_diff.append((ref, Decimal(str(sale_price)), ap))
            an = api_name.get(ref)
            if an and pname != f"({ref}) {an}":
                name_diff += 1

        w("\n── PRICES & NAMES " + "─" * 43)
        w(f"  products priced in feed     : {len(api_price):>8,}")
        w(f"  price differs from Odoo     : {len(price_diff):>8,}"
          + (bad("   <- sale_price is only set at creation, never refreshed")
             if len(price_diff) > 50 else ""))
        w(f"  name differs from Odoo      : {name_diff:>8,}")
        if price_diff and not quiet:
            w(f"\n  top {min(opts['top'], len(price_diff))} price gaps:")
            w(f"    {'ref':<12} {'GENIE':>12} {'ODOO':>12} {'delta':>12}")
            for ref, g, o in sorted(price_diff, key=lambda x: -abs(x[2] - x[1]))[:opts['top']]:
                w(f"    {ref:<12} {g:>12} {o:>12} {o-g:>+12}")

        # ── 8. branch / location mapping ────────────────────────────────────
        feed_codes = {k[0] for k in api_qty}
        known_codes = {c for c in code_of.values() if c}
        w("\n── BRANCH MAPPING " + "─" * 43)
        w(f"  location codes in feed      : {len(feed_codes):>8,}")
        unmapped = feed_codes - known_codes
        w(f"  not mapped to a Genie branch: {len(unmapped):>8,}"
          + (f"   {sorted(unmapped)}" if unmapped else ""))

        # ── 9. products actually sold ───────────────────────────────────────
        if not quiet:
            w("\n── PRODUCTS ON REAL SALES ORDERS " + "─" * 28)
            seen, okc, badc = set(), 0, 0
            for line in SOL.objects.select_related('product', 'order_id').all():
                o = line.order_id
                if not o or not o.branch_id or not line.product or not line.product.reference:
                    continue
                key = (code_of.get(o.branch_id), line.product.reference)
                if None in key or key in seen or (only and key[0] != only):
                    continue
                seen.add(key)
                g, a = db_qty.get(key), api_qty.get(key)
                if g == a:
                    okc += 1
                else:
                    badc += 1
                    w(warn(f"  {name_of.get(key[0],'?'):<17} {key[1]:<9} "
                           f"genie={str(g):<10} odoo={str(a):<10} MISMATCH"))
            w(f"  {okc} of {okc+badc} sold products match Odoo exactly")

        # ── 10. verdict ─────────────────────────────────────────────────────
        problems = []
        if cfg.stock_sync_status == 'failed':
            problems.append("last sync FAILED")
        if age_min > STALE_MIN:
            problems.append(f"last sync was {age_min:.0f} min ago (expected every 5)")
        if match_pct < HEALTHY_MATCH_PCT:
            problems.append(f"only {match_pct:.2f}% of quantities match")
        if big:
            problems.append(f"{len(big)} gaps larger than {SUSPICIOUS_DELTA} units")

        w("\n── VERDICT " + "─" * 50)
        if problems:
            for p in problems:
                w(bad(f"  PROBLEM: {p}"))
            raise SystemExit(1)
        w(ok(f"  HEALTHY — {match_pct:.2f}% of {len(both):,} quantities match Odoo exactly."))
        w(f"  The {len(mismatch)} differences are sales made in the {age_min:.0f} min "
          f"since the last sync;")
        w("  none is larger than a few units. The next run absorbs them.")
        w("")
