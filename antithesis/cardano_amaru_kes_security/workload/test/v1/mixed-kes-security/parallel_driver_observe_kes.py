#!/usr/bin/env python3
"""Observe one paired hot-KES attack while faults remain enabled."""

import os

from kes_observer import run_bounded


if __name__ == "__main__":
    run_bounded("parallel", float(os.environ.get("KES_OBSERVE_SECONDS", "30")))
