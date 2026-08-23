"""Differential Antithesis oracle for the runtime-bootstrapped DWARF/Amaru net."""
import os
import re
import time

try:
    from antithesis.assertions import always, reachable, sometimes
except ImportError:  # Unit-test/local parser use; the image always installs the SDK.
    def always(condition, message, details):
        return condition

    def sometimes(condition, message, details):
        return condition

    def reachable(message, details):
        return None


ADOPT_RE = re.compile(
    r"tip\.adopt slot=(?P<slot>\d+) header_hash=(?P<hash>[0-9a-f]+).*?block_height=(?P<height>\d+)"
    r"|adopted tip tip\.slot=(?P<slot2>\d+) tip\.hash=(?P<hash2>[0-9a-f]+).*?block_height=(?P<height2>\d+)"
)
DECODE_RE = re.compile(r"failed to decode message from network|invalid cbor|decode error", re.I)
PANIC_RE = re.compile(r"\bpanic(?:ked)?\b|\bfatal\b|amaru::fatal", re.I)


class OracleState:
    def __init__(self):
        self.control_by_height = {}
        self.control_baseline = None
        self.control_max = 0
        self.target_baseline = None
        self.target_max = 0
        self.control_advanced = False
        self.target_advanced = False
        self.target_rejected_mutation = False
        self.target_panicked = False
        self.control_panicked = False
        self.violations = []

    @staticmethod
    def adoptions(text):
        for match in ADOPT_RE.finditer(text):
            yield int(match.group("height") or match.group("height2")), match.group("hash") or match.group("hash2")

    def ingest_control(self, text):
        for height, block_hash in self.adoptions(text):
            if self.control_baseline is None:
                self.control_baseline = height
            self.control_by_height[height] = block_hash
            self.control_max = max(self.control_max, height)
        self.control_advanced = self.control_baseline is not None and self.control_max > self.control_baseline
        self.control_panicked |= bool(PANIC_RE.search(text))

    def ingest_target(self, text):
        self.target_rejected_mutation |= bool(DECODE_RE.search(text))
        self.target_panicked |= bool(PANIC_RE.search(text))
        for height, block_hash in self.adoptions(text):
            if self.target_baseline is None:
                self.target_baseline = height
            self.target_max = max(self.target_max, height)
        self.target_advanced = self.target_baseline is not None and self.target_max > self.target_baseline


def appended(path, offsets):
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    offset = offsets.get(path, 0)
    if size < offset:
        offset = 0
    with open(path, encoding="utf-8", errors="replace") as stream:
        stream.seek(offset)
        data = stream.read()
        offsets[path] = stream.tell()
    return data


def main():
    log_dir = os.environ.get("AMARU_LOG_DIR", "/opt/amaru-logs")
    target = os.path.join(log_dir, os.environ.get("TARGET_RELAY_LOG", "amaru-relay-1.log"))
    control = os.path.join(log_dir, os.environ.get("CONTROL_RELAY_LOG", "amaru-relay-2.log"))
    poll = float(os.environ.get("ORACLE_POLL_SECS", "5"))
    state, offsets = OracleState(), {}
    while True:
        state.ingest_control(appended(control, offsets))
        state.ingest_target(appended(target, offsets))
        always(not state.target_panicked, "DWARF input does not panic target Amaru", {})
        always(not state.control_panicked, "honest control Amaru does not panic", {})
        sometimes(state.control_advanced, "honest Amaru control advances after runtime bootstrap", {"height": state.control_max})
        sometimes(state.target_advanced, "DWARF target Amaru advances after runtime bootstrap", {"height": state.target_max})
        sometimes(state.target_rejected_mutation, "DWARF mutation reaches and is rejected by Amaru decoding", {})
        if state.target_rejected_mutation:
            reachable("DWARF-to-Amaru mutation path exercised", {})
        time.sleep(poll)


if __name__ == "__main__":
    main()
