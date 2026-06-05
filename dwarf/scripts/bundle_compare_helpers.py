from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_for_compare(value, *, left_run_dir: Path, right_run_dir: Path):
    left_prefix = str(left_run_dir)
    right_prefix = str(right_run_dir)
    if isinstance(value, dict):
        return {
            key: normalize_for_compare(item, left_run_dir=left_run_dir, right_run_dir=right_run_dir)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            normalize_for_compare(item, left_run_dir=left_run_dir, right_run_dir=right_run_dir)
            for item in value
        ]
    if isinstance(value, str):
        if value.startswith(left_prefix):
            return "<run_dir>" + value[len(left_prefix):]
        if value.startswith(right_prefix):
            return "<run_dir>" + value[len(right_prefix):]
    return value


def compare_relpath(*, left_run_dir: Path, right_run_dir: Path, relpath: str) -> dict:
    left_path = left_run_dir / relpath
    right_path = right_run_dir / relpath
    result = {
        "relpath": relpath,
        "left_exists": left_path.is_file(),
        "right_exists": right_path.is_file(),
        "verdict": "diff",
    }
    if not left_path.is_file() and not right_path.is_file():
        result["verdict"] = "missing_in_both"
        return result
    if not left_path.is_file():
        result["verdict"] = "missing_in_left"
        return result
    if not right_path.is_file():
        result["verdict"] = "missing_in_right"
        return result

    result["left_sha256"] = sha256_file(left_path)
    result["right_sha256"] = sha256_file(right_path)

    try:
        left_json = load_json(left_path)
        right_json = load_json(right_path)
    except json.JSONDecodeError:
        result["reason"] = "non_json_artifact"
        result["verdict"] = "match" if result["left_sha256"] == result["right_sha256"] else "diff"
        result["normalized_equal"] = result["verdict"] == "match"
        return result

    normalized_left = normalize_for_compare(
        left_json,
        left_run_dir=left_run_dir,
        right_run_dir=right_run_dir,
    )
    normalized_right = normalize_for_compare(
        right_json,
        left_run_dir=left_run_dir,
        right_run_dir=right_run_dir,
    )
    result["normalized_equal"] = normalized_left == normalized_right
    result["verdict"] = "match" if result["normalized_equal"] else "diff"
    if not result["normalized_equal"]:
        result["left_normalized"] = normalized_left
        result["right_normalized"] = normalized_right
    return result
