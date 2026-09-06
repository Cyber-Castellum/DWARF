#!/usr/bin/env python3
"""Sample the non-adoption invariant concurrently with drivers and faults."""

import os

from kes_observer import run_bounded


if __name__ == "__main__":
    run_bounded("anytime", float(os.environ.get("KES_ANYTIME_SECONDS", "10")))
