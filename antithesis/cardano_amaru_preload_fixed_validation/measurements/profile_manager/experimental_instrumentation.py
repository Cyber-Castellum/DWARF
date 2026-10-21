"""Identity bindings for unqualified, controller-built Amaru instrumentation."""
import re

TEMPLATE_ID = "amaru-nanoseconds-v3-20260918"
PATCH_SET = "47787f152bc7bdcd645d5d32125f6658847812c447fd375ede44d31b546620f6"
CAPABILITIES = frozenset({"amaru-patch-identity", "amaru-measurement-patch-protocol-decode",
    "amaru-measurement-patch-blockfetch", "amaru-measurement-patch-txsubmission2"})
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def validate_candidate(candidate, target):
    fields = {"source_revision", "template_id", "measurement_revision", "patch_set_sha256",
              "evidence_sha256", "controller_installation_id", "config_image_id", "executable_digest"}
    if not isinstance(candidate, dict) or set(candidate) != fields:
        raise ValueError("experimental instrumentation identity is invalid")
    if (candidate["template_id"] != TEMPLATE_ID or candidate["measurement_revision"] != "nanoseconds-v3"
            or candidate["patch_set_sha256"] != PATCH_SET
            or not re.fullmatch(r"[0-9a-f]{40}", str(candidate["source_revision"]))
            or any(not _DIGEST.fullmatch(str(candidate[key])) for key in
                   ("evidence_sha256", "config_image_id", "executable_digest"))
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(candidate["controller_installation_id"]))):
        raise ValueError("experimental instrumentation identity is invalid")
    expected = {
        "source_revision": target.get("source_revision"),
        "config_image_id": target.get("image_config_id", target.get("config_image_id")),
        "executable_digest": target.get("executable_digest"),
        "controller_installation_id": target.get("controller_installation_id"),
    }
    if any(candidate[key] != value for key, value in expected.items()):
        raise ValueError("experimental instrumentation does not match target identity")
    return dict(candidate)
