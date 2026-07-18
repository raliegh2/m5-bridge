"""Strict live export for V14.16 quality allocation.

The implementation module resolves ``quality_risk_target`` at runtime. Replace
that symbol with the frozen-nominal wrapper before exporting the executor so a
live signal already reduced by an upstream profile cannot be promoted.
"""
from __future__ import annotations

from . import v14_16_quality_allocation_execution as implementation
from .v14_16_quality_nominal import strict_quality_risk_target

implementation.quality_risk_target = strict_quality_risk_target
QualityAllocationLiveExecutor = implementation.QualityAllocationLiveExecutor

__all__ = ["QualityAllocationLiveExecutor"]
