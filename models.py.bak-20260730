# -*- coding: utf-8 -*-
from django.db import models
from decimal import Decimal
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from modules.base.models.base import BaseModel


class BranchProductStock(BaseModel):
    """
    Simple per-branch on-hand stock tracker.
    Populated by the Alfouad API stock sync.
    """

    branch = models.ForeignKey(
        'base.branch',
        on_delete=models.CASCADE,
        related_name='el_fouad_product_stocks',
        verbose_name=_("Branch"),
    )

    product = models.ForeignKey(
        'products.producttemplate',
        on_delete=models.CASCADE,
        related_name='el_fouad_branch_stocks',
        verbose_name=_("Product"),
    )

    on_hand = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name=_("On Hand"),
        help_text=_("Quantity physically available at this branch"),
    )

    class Meta:
        unique_together = [('branch', 'product')]
        verbose_name = _("Branch Product Stock")
        verbose_name_plural = _("Branch Product Stocks")
        ordering = ['branch', 'product']

    def __str__(self):
        return f"{self.product.name} @ {self.branch.name}: {self.on_hand}"


class AlfouadAPIConfig(BaseModel):
    """
    Singleton-style configuration for the Alfouad Pharmacies API integration.
    Create one record and set enabled=True to activate the sync.
    """

    SYNC_STATUS = [
        ('idle',    'Idle'),
        ('running', 'Running'),
        ('failed',  'Failed'),
    ]

    api_key = models.CharField(
        max_length=255,
        verbose_name=_("API Key"),
        help_text=_("Api-Key header value for Alfouad API"),
    )

    base_url = models.CharField(
        max_length=255,
        default='https://alfouadpharmacies.roayatec.com',
        verbose_name=_("Base URL"),
    )

    enabled = models.BooleanField(
        default=True,
        verbose_name=_("Enabled"),
        help_text=_("Disable to pause all background syncs"),
    )

    sync_interval_minutes = models.PositiveIntegerField(
        default=5,
        verbose_name=_("Sync Interval (minutes)"),
        help_text=_("How often the stock sync Celery task runs"),
    )

    chunk_size = models.PositiveIntegerField(
        default=500,
        verbose_name=_("Bulk Chunk Size"),
        help_text=_("Records processed per bulk_create / bulk_update call"),
    )

    last_stock_sync = models.DateTimeField(
        null=True, blank=True,
        verbose_name=_("Last Stock Sync"),
    )

    last_locations_sync = models.DateTimeField(
        null=True, blank=True,
        verbose_name=_("Last Locations Sync"),
    )

    stock_sync_status = models.CharField(
        max_length=20,
        choices=SYNC_STATUS,
        default='idle',
        verbose_name=_("Stock Sync Status"),
    )

    last_error = models.TextField(
        blank=True, default='',
        verbose_name=_("Last Error"),
        help_text=_("Most recent error from a failed sync"),
    )

    class Meta:
        verbose_name = _("Alfouad API Config")
        verbose_name_plural = _("Alfouad API Configs")

    def __str__(self):
        return f"Alfouad API ({self.base_url}) — {'enabled' if self.enabled else 'disabled'}"

    def mark_sync_start(self):
        self.stock_sync_status = 'running'
        self.last_error = ''
        self.save(update_fields=['stock_sync_status', 'last_error'])

    def mark_sync_done(self):
        self.stock_sync_status = 'idle'
        self.last_stock_sync = timezone.now()
        self.save(update_fields=['stock_sync_status', 'last_stock_sync'])

    def mark_sync_failed(self, error: str):
        self.stock_sync_status = 'failed'
        self.last_error = error
        self.save(update_fields=['stock_sync_status', 'last_error'])
