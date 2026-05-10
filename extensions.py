# -*- coding: utf-8 -*-
from modules.base.model_inheritance import ModelExtension
from modules.base.decorators import onchange, action
from django.db import models
from django.utils.translation import gettext_lazy as _
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


def _products_for_branch(branch_id):
    """Genie domain: products available at branch_id, or unrestricted."""
    if not branch_id:
        return {'field': 'id', 'operator': 'eq', 'value': 0}  # no branch → show nothing
    return {
        'operator': 'or',
        'filters': [
            {'field': 'allowed_branches', 'operator': 'is_null'},
            {'field': 'allowed_branches', 'operator': 'in', 'value': [branch_id]},
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Branch extension — Alfouad location code
# ═══════════════════════════════════════════════════════════════════════════

class BranchElFouadExtension(ModelExtension):
    _inherit = 'base.branch'
    _depends = ['el_fouad']

    location_code = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        unique=True,
        verbose_name=_("Alfouad Location Code"),
        help_text=_("API location_code from Alfouad Pharmacies (e.g. BT456)"),
    )


# ═══════════════════════════════════════════════════════════════════════════
# SalesOrder extension
# ═══════════════════════════════════════════════════════════════════════════

class SalesOrderElFouadExtension(ModelExtension):
    """
    Header-level:
    - Alfouad sync state fields
    - Branch change → live product domain for the whole order_lines list
    - Live totals recompute when any line field changes
    - on_hand_qty refresh via formChange when product changes
    """
    _inherit = 'sales.salesorder'
    _depends = ['sales', 'el_fouad']

    # Values ARE the display text — status widget + ribbon read them directly.
    # Progression: Genie (default) → Posted to Odoo  |  Post Failed (retry allowed)
    ALFOUAD_SYNC_STATES = [
        ('Genie',          'Genie'),
        ('Posted to Odoo', 'Posted to Odoo'),
        ('Post Failed',    'Post Failed'),
    ]

    alfouad_order_id = models.IntegerField(
        null=True, blank=True, default=None,
        verbose_name=_("Alfouad Order ID"),
    )
    alfouad_order_name = models.CharField(
        max_length=64, null=True, blank=True, default=None,
        verbose_name=_("Alfouad Order Ref"),
    )
    alfouad_sync_state = models.CharField(
        max_length=20, choices=ALFOUAD_SYNC_STATES,
        null=True, blank=True, default='Genie',
        verbose_name=_("Alfouad Sync"),
    )
    alfouad_posted_at = models.DateTimeField(
        null=True, blank=True, default=None,
        verbose_name=_("Posted to Odoo At"),
        help_text=_("Timestamp when this order was pushed to Alfouad Odoo"),
    )

    @action
    def action_post_to_odoo(queryset):
        """
        Push this sales order to Alfouad's Odoo via POST /api/create_sale_order.
        Sets alfouad_sync_state to 'Posted to Odoo' or 'Post Failed' accordingly.
        """
        from django.utils import timezone as tz
        from el_fouad.services.alfouad_api import create_sale_order

        records = list(queryset)
        if not records:
            return {'status': False, 'open_mode': 'message',
                    'message': _('No order selected.'), 'data': {}}

        order = records[0]

        # ── Validate ──────────────────────────────────────────────────────
        if not order.branch_id:
            return {'status': False, 'open_mode': 'message',
                    'message': _('Please select a branch before posting.'), 'data': {}}

        if not order.partner_id:
            return {'status': False, 'open_mode': 'message',
                    'message': _('Please select a customer before posting.'), 'data': {}}

        lines = list(order.order_lines.select_related('product').all())
        valid_lines = [l for l in lines if l.product and l.product.reference]
        if not valid_lines:
            return {'status': False, 'open_mode': 'message',
                    'message': _('No order lines with a valid product reference found.'), 'data': {}}

        # ── Build payload ─────────────────────────────────────────────────
        partner = order.partner
        payload = {
            'customer': {
                'mobile': getattr(partner, 'mobile', '') or getattr(partner, 'phone', '') or '',
                'name':   partner.name or '',
                'phone':  getattr(partner, 'phone', '') or '',
                'email':  getattr(partner, 'email', '') or '',
                'street': getattr(partner, 'street', '') or '',
                'street2': getattr(partner, 'street2', '') or '',
                'city':   getattr(partner, 'city', '') or '',
            },
            'location_code': order.branch.location_code or '',
            'order_date': str(order.date_order.date()) if order.date_order else '',
            'note': getattr(order, 'note', '') or '',
            'order_lines': [
                {
                    'product_ref': line.product.reference,
                    'qty':         float(line.product_uom_qty),
                    'price_unit':  float(line.price_unit),
                    'discount':    float(line.discount),
                }
                for line in valid_lines
            ],
        }

        # ── Call API ──────────────────────────────────────────────────────
        response, error = create_sale_order(payload)

        # Use QuerySet.update() — direct SQL, always reliable for extension fields
        from modules.sales.models.order import SalesOrder as _SO

        if error:
            _SO.objects.filter(pk=order.pk).update(alfouad_sync_state='Post Failed')
            return {
                'status': False,
                'open_mode': 'message',
                'message': str(_('Post Failed: %(e)s') % {'e': error}),
                'data': {},
                'on_success': {'type': 'refresh'},
            }

        _SO.objects.filter(pk=order.pk).update(
            alfouad_order_id   = response.get('sale_order_id'),
            alfouad_order_name = response.get('name'),
            alfouad_sync_state = 'Posted to Odoo',
            alfouad_posted_at  = tz.now(),
        )

        return {
            'status': True,
            'open_mode': 'message',
            'message': str(_(
                '✓ Order %(ref)s posted to Alfouad Odoo — Reference: %(odoo_ref)s'
            ) % {
                'ref':      order.name,
                'odoo_ref': response.get('name', ''),
            }),
            'data': {},
            'on_success': {'type': 'refresh'},
        }

    @onchange('branch')
    def _onchange_branch_domain(self):
        """
        When header branch changes:
        - Return live domain so order_lines.product only shows branch products
        - If no branch: domain blocks all products
        """
        return {
            'domain': {
                'order_lines.product': _products_for_branch(self.branch_id),
            }
        }

    @onchange(
        'order_lines.product_uom_qty',
        'order_lines.price_unit',
        'order_lines.discount',
        'order_lines.product',
    )
    def _onchange_lines_recompute_totals(self):
        """Recompute Untaxed Amount / Taxes / Total in real time."""
        lines = getattr(self, '_original_data', {}).get('order_lines', [])
        amount_untaxed = Decimal('0.00')
        amount_tax     = Decimal('0.00')

        for line in lines:
            if not isinstance(line, dict):
                continue
            qty      = Decimal(str(line.get('product_uom_qty') or 0))
            price    = Decimal(str(line.get('price_unit') or 0))
            discount = Decimal(str(line.get('discount') or 0))
            subtotal = qty * price * (1 - discount / 100)
            amount_untaxed += subtotal

            tax_data = line.get('tax_id') or []
            if isinstance(tax_data, list):
                try:
                    from modules.account.models import Tax
                    for t in tax_data:
                        tid = t.get('id') if isinstance(t, dict) else t
                        if not tid:
                            continue
                        tax_obj = Tax.objects.filter(pk=tid).first()
                        if tax_obj and hasattr(tax_obj, 'amount') and tax_obj.amount:
                            if getattr(tax_obj, 'amount_type', 'percent') == 'percent':
                                amount_tax += subtotal * Decimal(str(tax_obj.amount)) / 100
                            else:
                                amount_tax += Decimal(str(tax_obj.amount))
                except Exception:
                    pass

        self.amount_untaxed = amount_untaxed
        self.amount_tax     = amount_tax
        self.amount_total   = amount_untaxed + amount_tax

    @onchange('order_lines.product')
    def _onchange_order_line_refresh_on_hand(self):
        """
        formChange: refresh on_hand_qty for each line using the header branch.
        Covers new (unsaved) orders where the row has no order_id yet.
        """
        if not self.branch_id:
            return

        lines = getattr(self, '_original_data', {}).get('order_lines', [])
        if not lines:
            return

        try:
            from el_fouad.models import BranchProductStock
        except Exception:
            return

        updated = []
        for line in lines:
            if not isinstance(line, dict):
                updated.append(line)
                continue

            product_data = line.get('product')
            if isinstance(product_data, dict):
                product_id = product_data.get('id')
            elif isinstance(product_data, (int, str)) and product_data:
                try:
                    product_id = int(product_data)
                except Exception:
                    product_id = None
            else:
                product_id = None

            on_hand = Decimal('0.000')
            if product_id:
                stock = BranchProductStock.objects.filter(
                    branch_id=self.branch_id,
                    product_id=product_id,
                ).first()
                if stock:
                    on_hand = stock.on_hand

            updated.append({**line, 'on_hand_qty': float(on_hand)})

        return {'value': {'order_lines': updated}}


# ═══════════════════════════════════════════════════════════════════════════
# SalesOrderLine extension
# ═══════════════════════════════════════════════════════════════════════════

class SalesOrderLineElFouadExtension(ModelExtension):
    """
    Per-line:
    - on_hand_qty display field (populated from parent branch + product)
    - onChange on product → fill on_hand_qty from parent branch (saved orders)
    - onChange on qty → validate against on_hand_qty
    """
    _inherit = 'sales.salesorderline'
    _depends = ['sales', 'el_fouad']

    on_hand_qty = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name=_("Available"),
        help_text=_("Quantity available at the order's branch"),
    )

    def _get_branch_stock(self):
        """Look up BranchProductStock using the PARENT order's branch."""
        if not self.product_id:
            return None

        # For saved orders: read parent branch from DB
        branch_id = None
        if self.order_id_id:
            try:
                from modules.sales.models.order import SalesOrder
                order = SalesOrder.objects.select_related('branch').get(pk=self.order_id_id)
                branch_id = order.branch_id
            except Exception:
                pass
        elif hasattr(self, 'order_id') and self.order_id and self.order_id.branch_id:
            branch_id = self.order_id.branch_id

        if not branch_id:
            return None

        try:
            from el_fouad.models import BranchProductStock
            return BranchProductStock.objects.filter(
                branch_id=branch_id,
                product_id=self.product_id,
            ).first()
        except Exception as exc:
            logger.warning("BranchProductStock lookup failed: %s", exc)
            return None

    @onchange('product')
    def _onchange_product_on_hand(self):
        """Fill on_hand_qty when product changes (saved orders)."""
        stock = self._get_branch_stock()
        self.on_hand_qty = stock.on_hand if stock else Decimal('0.000')

    @onchange('product_uom_qty')
    def _onchange_qty_exceeds_on_hand(self):
        """Block quantities that exceed available stock."""
        try:
            ordered = Decimal(str(self.product_uom_qty or 0))
        except Exception:
            return
        if ordered <= Decimal('0'):
            return

        stock = self._get_branch_stock()
        on_hand = stock.on_hand if stock else Decimal('0.000')

        if ordered > on_hand:
            return {
                'errors': {
                    'product_uom_qty': _(
                        'Quantity %(ordered)s exceeds available stock (%(on_hand)s).'
                    ) % {'ordered': ordered, 'on_hand': on_hand}
                }
            }

    @action
    def action_check_stock(queryset):
        """Per-row button: open modal list of BranchProductStock for this product."""
        records = list(queryset)
        if not records:
            return {'status': False, 'open_mode': 'message',
                    'message': _('No line selected.'), 'data': {}}

        line = records[0]
        if not line.product_id:
            return {'status': False, 'open_mode': 'message',
                    'message': _('Please select a product on this line first.'), 'data': {}}

        try:
            product_name = line.product.name
        except Exception:
            product_name = str(line.product_id)

        return {
            'status': True,
            'open_mode': 'slideover',
            'message': _('Loading branch stock'),
            'data': {
                'menu_item_key': 'el_fouad_branch_stock_menu',
                'view_type': 'list',
                'domain': {
                    'filters': {
                        'operator': 'and',
                        'filters': [
                            {'field': 'product_id', 'operator': 'eq', 'value': line.product_id},
                        ],
                    }
                },
                'context': {},
                'type': 'action',
                'title': str(_('Available Stock — %(p)s') % {'p': product_name}),
            },
        }
