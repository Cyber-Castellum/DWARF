"""Collector interface helpers for version-pinned DWARF measurements.

Collectors are intentionally small adapters. They receive a bounded context,
write only inside their run-bundle directory, and return normalized samples to
the reporting layer. Concrete Amaru collectors live in later modules.
"""

