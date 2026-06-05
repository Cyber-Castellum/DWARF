"""Pure command-catalog helpers for the dashboard data layer."""
from __future__ import annotations


def _command_cards():
    return [
        ("Inspect current runtime", "./dwarf/cardano-profile inspect health --profile-id profile-a-haskell-peersharing-disabled"),
        ("Scan logs", "./dwarf/cardano-profile logs scan --profile-id profile-a-haskell-peersharing-disabled"),
        ("Run environment smoke", "./dwarf/cardano-profile test smoke run environment-smoke"),
        ("Run Package A Layer 1 fuzz", "./dwarf/cardano-profile fuzz run package-a-parser-protocol-layer1"),
        ("Run Package B live churn fuzz", "./dwarf/cardano-profile fuzz run package-b-live-connection-churn-layer3 --approve"),
        ("Show Package C evidence status", "./dwarf/cardano-profile evidence status package-c"),
        ("Compare Profile A/B", "./dwarf/cardano-profile diff profile-a-haskell-peersharing-disabled profile-b-haskell-peersharing-enabled"),
    ]


def _command_rows():
    return [{"label": label, "command": command} for label, command in _command_cards()]
