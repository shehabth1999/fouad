# -*- coding: utf-8 -*-
from django.utils.translation import gettext as _

menu_dict = {
    "el_fouad_main_menu": {
        "name": _("El Fouad"),
        "icon": "Store",
        "module": "el_fouad",
        "sequence": 110,
    },
    "el_fouad_branch_stock_menu": {
        "name": _("Branch Stock"),
        "icon": "Boxes",
        "module": "el_fouad",
        "model": "el_fouad.branchproductstock",
        "sequence": 1,
        "parent_key": "el_fouad_main_menu",
    },
}
