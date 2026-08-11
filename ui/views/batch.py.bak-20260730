# -*- coding: utf-8 -*-
from django.utils.translation import gettext as _

# ── Header branch field ──────────────────────────────────────────────────────
# Required once per order. onChange fires _onchange_branch_domain which sets
# the live product domain for the entire order_lines list.
_header_branch_field = {
    "name": "branch",
    "string": _("Branch"),
    "widget": "relation",
    "required": True,
    "readonly": False,
    "multiSelect": False,
    "onChange": True,
    "displayField": "name",
    "placeholder": _("Select branch first…"),
}

# ── Available (on-hand) column ───────────────────────────────────────────────
_on_hand_field = {
    "name": "order_lines.on_hand_qty",
    "widget": "number",
    "string": _("Available"),
    "required": False,
    "readonly": True,
    "width": 0.5,
}

# ── Check button column ──────────────────────────────────────────────────────
_check_btn_column = {
    "name": "order_lines.stock_check_actions",
    "string": _(""),
    "widget": "buttons",
    "editor": False,
    "visible": True,
    "width": 1,
    "buttons": [
        {
            "string": _("Check"),
            "color": "info",
            "size": "sm",
            "border": True,
            "fullColor": False,
            "action_name": "action_check_stock",
        }
    ],
}

# Initial product domain: show nothing until a branch is selected.
# _onchange_branch_domain on SalesOrder replaces this with branch-filtered results.
_no_products_domain = {"field": "id", "operator": "eq", "value": 0}

# ── Operations shared between both form views ────────────────────────────────
_shared_form_operations = [

    # ── Header ──────────────────────────────────────────────────────────────
    # Branch field — first in the main info group
    {
        "operation": "prepend",
        "target": "sheet.sections.0.groups.0.fields",
        "content": [_header_branch_field],
    },

    # Replace the default order-state status bar with the Alfouad sync status.
    # alfouad_sync_state choices: "Posted to Odoo" | "Post Failed"
    {
        "operation": "modify",
        "target": "header.status",
        "content": {
            "name":   "alfouad_sync_state",
            "widget": "status",
            "string": _("Alfouad Status"),
        },
    },

    # ── Ribbon ──────────────────────────────────────────────────────────────
    # Shows only when action has been taken (Posted or Failed).
    # Hidden while still in the default "Genie" state.
    {
        "operation": "modify",
        "target": "sheet",
        "content": {
            "ribbon": {
                "field_text": "alfouad_sync_state",
                "color": {
                    "success": "Posted to Odoo",
                    "danger":  "Post Failed",
                },
                "invisible": {
                    "field": "alfouad_sync_state",
                    "operator": "in",
                    "value": ["Genie", None],
                },
            }
        },
    },

    # ── Hide existing header action buttons (all except Cancel) ─────────────
    # Only from el_fouad's batch — base source files are NOT modified.
    {"operation": "modify", "target": "field[name=action_confirm]",          "content": {"invisible": True}},
    {"operation": "modify", "target": "field[name=action_print_order]",      "content": {"invisible": True}},
    {"operation": "modify", "target": "field[name=action_send_quotation]",   "content": {"invisible": True}},
    {"operation": "modify", "target": "field[name=action_quotation_send]",   "content": {"invisible": True}},
    {"operation": "modify", "target": "field[name=send_omise_payment_link]", "content": {"invisible": True}},

    # ── Add "Post to Odoo" action ────────────────────────────────────────────
    # Visible only on draft/sent orders that have NOT been posted yet.
    # Invisible once the order is already posted (ribbon takes over).
    {
        "operation": "append",
        "target": "header.actions",
        "content": [
            {
                "name": "action_post_to_odoo",
                "string": _("Post to Odoo"),
                "type": "server",
                "icon": "Send",
                "as": "button",
                "confirm_required": True,
                "confirm_color": "success",
                # Hide only when already posted successfully — show on Genie + Post Failed (retry)
                "invisible": {
                    "field": "alfouad_sync_state",
                    "operator": "eq",
                    "value": "Posted to Odoo",
                },
            }
        ],
    },

    # ── Alfouad tracking fields — main form section (after Branch) ───────────
    # alfouad_sync_state ALWAYS visible — shows current sync status to the user.
    # alfouad_posted_at also always visible (shows "—" when not posted yet).
    {
        "operation": "after",
        "target": "field[name=branch]",
        "content": {
            "name": "alfouad_sync_state",
            "string": _("Alfouad Status"),
            "widget": "select",
            "required": False,
            "readonly": True,
        },
    },
    {
        "operation": "after",
        "target": "field[name=alfouad_sync_state]",
        "content": {
            "name": "alfouad_posted_at",
            "string": _("Posted At"),
            "widget": "datetime",
            "required": False,
            "readonly": True,
        },
    },

    # ── Hide "Other Information" tab entirely ─────────────────────────────────
    {
        "operation": "modify",
        "target": "tab[title=Other Information]",
        "content": {"invisible": True},
    },

    # ── Hide fields the Alfouad API doesn't need ─────────────────────────────
    # Form fields use invisible:True  (NOT visible:False — that's list-view only)
    {"operation": "modify", "target": "field[name=pricelist_id]",            "content": {"invisible": True}},
    {"operation": "modify", "target": "field[name=quotation_template_id]",   "content": {"invisible": True}},
    {"operation": "modify", "target": "field[name=user_id]",                 "content": {"invisible": True}},
    {"operation": "modify", "target": "field[name=sales_agent]",             "content": {"invisible": True}},
    {"operation": "modify", "target": "field[name=team_id]",                 "content": {"invisible": True}},

    # ── Order Lines list ─────────────────────────────────────────────────────
    # Register action_check_stock so the per-row button can reference it
    {
        "operation": "modify",
        "target": "field[name=order_lines]",
        "content": {
            "action_buttons": [
                {
                    "name": "action_check_stock",
                    "string": _("Check Stock"),
                    "type": "server",
                    "as": "button",
                }
            ]
        },
    },

    # Available column — after product
    {
        "operation": "after",
        "target": "field[name=order_lines.product]",
        "content": _on_hand_field,
    },

    # Check button — after Available
    {
        "operation": "after",
        "target": "field[name=order_lines.on_hand_qty]",
        "content": _check_btn_column,
    },

    # Hide: description (name) and taxes
    # These are listConfig COLUMNS → use visible:False  (NOT invisible:True)
    # visible:False  = list-view column hiding
    # invisible:True = form-field hiding  (different renderer, different key)
    {"operation": "modify", "target": "field[name=order_lines.name]",   "content": {"visible": False}},
    {"operation": "modify", "target": "field[name=order_lines.tax_id]", "content": {"visible": False}},

    # Product picker: blocked until branch selected; formChange for totals
    # width: 1.5 → relation base(2) × 1.5 = 3.0  (wide)
    {
        "operation": "modify",
        "target": "field[name=order_lines.product]",
        "content": {
            "domain": _no_products_domain,
            "formChange": True,
            "width": 1.5,
        },
    },

    # Quantity: wider for 3 decimal places; formChange for live totals
    {
        "operation": "modify",
        "target": "field[name=order_lines.product_uom_qty]",
        "content": {"formChange": True, "width": 1.2},
    },

    # Unit price: compact + formChange
    {
        "operation": "modify",
        "target": "field[name=order_lines.price_unit]",
        "content": {"formChange": True, "width": 0.8},
    },

    # Discount: very compact + formChange
    {
        "operation": "modify",
        "target": "field[name=order_lines.discount]",
        "content": {"formChange": True, "width": 0.5},
    },

    # Subtotal: compact readonly
    {
        "operation": "modify",
        "target": "field[name=order_lines.price_subtotal]",
        "content": {"width": 0.8},
    },
]

# ── 1. Sales Order FORM view ─────────────────────────────────────────────────
salesorder_form_el_fouad_batch = {
    "key": "salesorder_form_el_fouad_batch",
    "name": "Sales Order Form — El Fouad",
    "model": "sales.salesorder",
    "view_type": "form",
    "priority": 30,
    "inherit_mode": "extension",
    "inherit_id": "salesorder_form_view",
    "module": "el_fouad",
    "inheritance_operations": _shared_form_operations,
}

# ── 2. Sales QUOTATION form view ─────────────────────────────────────────────
sales_quote_form_el_fouad_batch = {
    "key": "sales_quote_form_el_fouad_batch",
    "name": "Sales Quotation Form — El Fouad",
    "model": "sales.salesorder",
    "view_type": "form",
    "priority": 31,
    "inherit_mode": "extension",
    "inherit_id": "sales_quote_form_view",
    "module": "el_fouad",
    "inheritance_operations": _shared_form_operations,
}

# ── 3. Sales Order LIST view ─────────────────────────────────────────────────
salesorder_list_el_fouad_batch = {
    "key": "salesorder_list_el_fouad_batch",
    "name": "Sales Order List — El Fouad (branch column)",
    "model": "sales.salesorder",
    "view_type": "list",
    "priority": 30,
    "inherit_mode": "extension",
    "inherit_id": "salesorder_list_view",
    "module": "el_fouad",
    "inheritance_operations": [
        {
            "operation": "after",
            "target": "field[name=name]",
            "content": {
                "name": "branch",
                "widget": "relation",
                "string": _("Branch"),
                "displayField": "name",
                "multiSelect": False,
                "editor": False,
                "width": 1,
            },
        }
    ],
}
