#!/usr/bin/env python3
"""Inventory local design documents with stable hashes; no content retrieval or embeddings."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_EXTENSIONS = {
    ".md",
    ".txt",
    ".html",
    ".htm",
    ".json",
    ".yaml",
    ".yml",
    ".docx",
    ".pdf",
}

VERSION_RE = re.compile(
    r"(?i)(?:^|[\s._-])((?:v|version)[\s._-]*\d+(?:[._-]\d+)*)"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def possible_version(path: Path) -> str | None:
    match = VERSION_RE.search(path.stem)
    return match.group(1) if match else None


def load_previous(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {entry["path"]: entry for entry in payload.get("documents", [])}


def is_excluded(relative_path: str, patterns: list[str]) -> bool:
    normalized = relative_path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def scan_root(
    root: Path, extensions: set[str], exclude: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    documents: list[dict[str, Any]] = []
    errors: list[str] = []
    if not root.exists() or not root.is_dir():
        return documents, [f"Document root does not exist or is not a directory: {root}"]

    for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file() or path.suffix.casefold() not in extensions:
            continue
        relative = path.relative_to(root).as_posix()
        if is_excluded(relative, exclude):
            continue
        try:
            stat = path.stat()
            documents.append(
                {
                    "root": str(root.resolve()),
                    "path": str(path.resolve()),
                    "relative_path": relative,
                    "extension": path.suffix.casefold(),
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "sha256": sha256_file(path),
                    "possible_version": possible_version(path),
                }
            )
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return documents, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a complete manifest of local design documents."
    )
    parser.add_argument("--root", action="append", required=True, help="Document root")
    parser.add_argument("--output", required=True, help="Output manifest JSON")
    parser.add_argument("--previous", help="Previous manifest used to calculate a delta")
    parser.add_argument(
        "--extension",
        action="append",
        help="Allowed extension; repeat to override the defaults",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Relative glob to exclude, such as archive/**",
    )
    args = parser.parse_args()

    extensions = (
        {item if item.startswith(".") else f".{item}" for item in args.extension}
        if args.extension
        else DEFAULT_EXTENSIONS
    )
    extensions = {item.casefold() for item in extensions}

    previous_path = Path(args.previous).resolve() if args.previous else None
    previous = load_previous(previous_path)
    documents: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_paths: set[str] = set()

    for raw_root in args.root:
        root_documents, root_errors = scan_root(
            Path(raw_root).resolve(), extensions, args.exclude
        )
        for document in root_documents:
            if document["path"] not in seen_paths:
                documents.append(document)
                seen_paths.add(document["path"])
        errors.extend(root_errors)

    documents.sort(key=lambda entry: entry["path"].casefold())
    current = {entry["path"]: entry for entry in documents}
    added = sorted(path for path in current if path not in previous)
    removed = sorted(path for path in previous if path not in current)
    changed = sorted(
        path
        for path in current.keys() & previous.keys()
        if current[path].get("sha256") != previous[path].get("sha256")
    )
    unchanged = sorted(
        path
        for path in current.keys() & previous.keys()
        if current[path].get("sha256") == previous[path].get("sha256")
    )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "roots": [str(Path(root).resolve()) for root in args.root],
        "extensions": sorted(extensions),
        "documents": documents,
        "delta": {
            "added": added,
            "changed": changed,
            "unchanged": unchanged,
            "removed": removed,
        },
        "errors": errors,
    }

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "documents": len(documents),
                "added": len(added),
                "changed": len(changed),
                "unchanged": len(unchanged),
                "removed": len(removed),
                "errors": len(errors),
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
