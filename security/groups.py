# -*- coding: utf-8 -*-
"""
Security groups for el_fouad module

This file defines all security groups for the el_fouad module.
Groups are synced to the database using the sync_groups management command.
"""

GROUPS = [
    {
        'name': 'El_fouad Users',
        'technical_name': 'el_fouad.users',
        'category': 'El_fouad',
        'description': 'Access el_fouad module',
    },
    {
        'name': 'El_fouad Admins',
        'technical_name': 'el_fouad.admins',
        'category': 'El_fouad',
        'implied_groups': ['el_fouad.users'],
        'description': 'Manage all el_fouad module',
    }
]
