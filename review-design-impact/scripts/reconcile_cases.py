#!/usr/bin/env python3
"""Reconcile generated ChangeCase files with the active document manifest."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


def normalized_path(value: str | Path) -> str:
    return str(Path(value).resolve()).casefold()


def load_manifest(path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    active: dict[str, str] = {}
    paths_by_hash: dict[str, list[str]] = {}
    for document in payload.get("documents", []):
        if not isinstance(document, dict):
            continue
        source_path = document.get("path")
        digest = document.get("sha256")
        if isinstance(source_path, str) and isinstance(digest, str):
            active[normalized_path(source_path)] = digest.casefold()
            paths_by_hash.setdefault(digest.casefold(), []).append(str(Path(source_path).resolve()))
    return active, paths_by_hash


def load_case_file(path: Path) -> tuple[str, list[Any]]:
    if path.suffix.casefold() == ".jsonl":
        values = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
        return "jsonl", values
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(value, list):
        return "array", value
    return "object", [value]


def render_case_file(kind: str, values: list[Any]) -> str:
    if kind == "jsonl":
        return "".join(
            f"{json.dumps(value, ensure_ascii=False, sort_keys=True)}\n"
            for value in values
        )
    if kind == "array":
        return json.dumps(values, ensure_ascii=False, indent=2)
    return json.dumps(values[0], ensure_ascii=False, indent=2)


def atomic_write(path: Path, content: str) -> None:
    handle, temp_name = tempfile.mkstemp(
        prefix=f"{path.stem}-", suffix=".tmp", dir=path.parent
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(content, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def classify_case(
    case: Any,
    active: dict[str, str],
    paths_by_hash: dict[str, list[str]],
) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(case, dict):
        return "invalid", None
    source = case.get("source")
    if not isinstance(source, dict):
        return "invalid", None
    source_path = source.get("path")
    digest = source.get("sha256")
    if not isinstance(source_path, str) or not isinstance(digest, str):
        return "invalid", None
    path_key = normalized_path(source_path)
    digest_key = digest.casefold()
    if active.get(path_key) == digest_key:
        return "active", case
    destinations = paths_by_hash.get(digest_key, [])
    if path_key not in active and len(destinations) == 1:
        migrated = json.loads(json.dumps(case, ensure_ascii=False))
        migrated["source"]["path"] = destinations[0]
        return "moved", migrated
    if path_key in active:
        return "changed", None
    return "removed", None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove or migrate generated cases that no longer match the manifest."
    )
    parser.add_argument("--cases", required=True, help="Generated ChangeCase directory")
    parser.add_argument("--manifest", required=True, help="Active document manifest")
    parser.add_argument("--index", help="Output reverse index JSON")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cases_root = Path(args.cases).resolve()
    manifest_path = Path(args.manifest).resolve()
    if not cases_root.is_dir() or not manifest_path.is_file():
        print("Cases directory or manifest is missing.", file=sys.stderr)
        return 2
    index_path = (
        Path(args.index).resolve()
        if args.index
        else cases_root.parent / "case-index.json"
    )

    try:
        active, paths_by_hash = load_manifest(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    stats = {"active": 0, "moved": 0, "changed": 0, "removed": 0, "invalid": 0}
    errors: list[str] = []
    reverse_index: dict[str, list[dict[str, str]]] = {}
    paths = sorted(
        [*cases_root.rglob("*.json"), *cases_root.rglob("*.jsonl")],
        key=lambda item: str(item).casefold(),
    )
    for path in paths:
        try:
            kind, values = load_case_file(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        retained: list[dict[str, Any]] = []
        changed_file = False
        for value in values:
            status, reconciled = classify_case(value, active, paths_by_hash)
            stats[status] += 1
            if status in {"active", "moved"} and reconciled is not None:
                retained.append(reconciled)
                changed_file = changed_file or status == "moved"
                source = reconciled["source"]
                key = f"{normalized_path(source['path'])}|{source['sha256'].casefold()}"
                reverse_index.setdefault(key, []).append(
                    {
                        "case_id": str(reconciled.get("case_id", "")),
                        "case_file": str(path),
                    }
                )
            else:
                changed_file = True
        if args.dry_run or not changed_file:
            continue
        if retained:
            atomic_write(path, render_case_file(kind, retained))
        else:
            path.unlink()

    index_payload = {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "documents": reverse_index,
        "stats": stats,
        "errors": errors,
    }
    if not args.dry_run:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            index_path,
            json.dumps(index_payload, ensure_ascii=False, indent=2),
        )
    print(json.dumps(index_payload, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
