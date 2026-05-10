# -*- coding: utf-8 -*-
{
    'name': 'El Fouad',
    'technical_name': 'el_fouad',
    'type': 'app',
    'summary': 'Branch-aware sales orders with per-branch on-hand stock tracking',
    'description': """
El Fouad module:
- Adds Branch field (required) to Sales Orders
- Resets order lines when branch changes
- Filters product picker by selected branch
- Tracks per-branch on-hand quantity (BranchProductStock)
- Shows on_hand_qty per branch in order lines
""",
    'author': "Genie ERP",
    'website': "https://www.aigeniecrm.com",
    'category': 'Sales',
    'version': '0.0.1',
    'application': True,
    'installable': True,
    'auto_install': False,
    'depends': ['sales', 'products'],
}
