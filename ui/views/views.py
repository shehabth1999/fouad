# -*- coding: utf-8 -*-
from django.utils.translation import gettext as _

branch_product_stock_list_view = {
    "key": "el_fouad_branch_product_stock_list",
    "name": "Branch Product Stock",
    "model": "el_fouad.branchproductstock",
    "view_type": "list",
    "priority": 10,
    "module": "el_fouad",
    "menu_item": "el_fouad_branch_stock_menu",
    "body": {
        "tree": {
            "header": {
                "actions": []
            },
            "fields": [
                {
                    "name": "branch",
                    "widget": "relation",
                    "string": _("Branch"),
                    "displayField": "name",
                    "multiSelect": False,
                    "editor": True,
                    "required": True,
                    "width": 200,
                },
                {
                    "name": "product",
                    "widget": "relation",
                    "string": _("Product"),
                    "displayField": "name",
                    "multiSelect": False,
                    "editor": True,
                    "required": True,
                    "width": 250,
                },
                {
                    "name": "on_hand",
                    "widget": "number",
                    "string": _("On Hand"),
                    "editor": True,
                    "required": False,
                    "width": 130,
                },
            ],
        }
    },
}
