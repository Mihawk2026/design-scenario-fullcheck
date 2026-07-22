#!/usr/bin/env python3
"""Create or refresh hidden local state for the natural-language skill workflow."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from inventory_documents import DEFAULT_EXTENSIONS, scan_root


DESIGN_TOKENS = {
    "design",
    "designs",
    "docs",
    "architecture",
    "architectures",
    "adr",
    "rfc",
    "spec",
    "specs",
    "设计",
    "方案",
    "架构",
    "改造",
    "需求",
    "技术文档",
}

EXCLUDED_PARTS = {
    ".git",
    ".design-impact",
    ".idea",
    ".vscode",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "coverage",
    "__pycache__",
    "review-design-impact",
}


def normalize_path(path: str | Path) -> str:
    return str(Path(path).resolve()).casefold()


def has_excluded_part(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return any(part.casefold() in EXCLUDED_PARTS for part in relative.parts)


def design_score(path: Path, root: Path) -> int:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    score = 0
    for part in relative.parts:
        folded = part.casefold()
        if folded in DESIGN_TOKENS:
            score += 3
        if any(token in folded for token in DESIGN_TOKENS):
            score += 1
    return score


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def iter_case_objects(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.casefold() == ".jsonl":
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value
        return
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item


def extracted_sources(cases_dir: Path) -> tuple[dict[str, set[str]], list[str], int]:
    sources: dict[str, set[str]] = {}
    errors: list[str] = []
    files = sorted(
        [*cases_dir.rglob("*.json"), *cases_dir.rglob("*.jsonl")],
        key=lambda item: str(item).casefold(),
    )
    for path in files:
        try:
            for case in iter_case_objects(path):
                source = case.get("source", {})
                source_path = source.get("path")
                source_hash = source.get("sha256")
                if isinstance(source_path, str) and isinstance(source_hash, str):
                    sources.setdefault(normalize_path(source_path), set()).add(
                        source_hash.casefold()
                    )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: {exc}")
    return sources, errors, len(files)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automatically discover design documents and prepare skill state."
    )
    parser.add_argument(
        "--workspace", default=".", help="Active workspace; defaults to current directory"
    )
    parser.add_argument(
        "--state-dir",
        help="Generated state directory; defaults to <workspace>/.design-impact",
    )
    parser.add_argument(
        "--document-root",
        action="append",
        help="Optional explicit document root; repeat for multiple roots",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"Workspace does not exist or is not a directory: {workspace}", file=sys.stderr)
        return 2
    state_dir = (
        Path(args.state_dir).resolve()
        if args.state_dir
        else workspace / ".design-impact"
    )
    cases_dir = state_dir / "cases"
    reports_dir = state_dir / "reports"
    cases_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    explicit_roots = bool(args.document_root)
    roots = (
        [Path(value).resolve() for value in args.document_root]
        if explicit_roots
        else [workspace]
    )
    all_documents: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()

    for root in roots:
        documents, scan_errors = scan_root(root, DEFAULT_EXTENSIONS, [])
        errors.extend(scan_errors)
        for document in documents:
            path = Path(document["path"])
            if has_excluded_part(path, workspace):
                continue
            key = normalize_path(path)
            if key not in seen:
                document["design_score"] = design_score(path, root)
                all_documents.append(document)
                seen.add(key)

    design_documents = [item for item in all_documents if item["design_score"] > 0]
    if explicit_roots:
        selected = all_documents
        discovery_mode = "explicit-roots"
    elif design_documents:
        selected = design_documents
        discovery_mode = "design-name-or-directory"
    else:
        selected = [
            item
            for item in all_documents
            if Path(item["path"]).name.casefold() not in {"readme.md", "license.md"}
        ]
        discovery_mode = "fallback-all-supported-documents"

    selected.sort(key=lambda item: item["path"].casefold())
    manifest_path = state_dir / "manifest.json"
    previous = load_manifest(manifest_path)
    previous_docs = {
        normalize_path(item["path"]): item
        for item in previous.get("documents", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    current_docs = {normalize_path(item["path"]): item for item in selected}

    added = sorted(
        current_docs[key]["path"] for key in current_docs.keys() - previous_docs.keys()
    )
    removed = sorted(
        previous_docs[key]["path"] for key in previous_docs.keys() - current_docs.keys()
    )
    changed = sorted(
        current_docs[key]["path"]
        for key in current_docs.keys() & previous_docs.keys()
        if current_docs[key].get("sha256") != previous_docs[key].get("sha256")
    )
    unchanged = sorted(
        current_docs[key]["path"]
        for key in current_docs.keys() & previous_docs.keys()
        if current_docs[key].get("sha256") == previous_docs[key].get("sha256")
    )

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "workspace": str(workspace),
        "roots": [str(root) for root in roots],
        "discovery_mode": discovery_mode,
        "documents": selected,
        "delta": {
            "added": added,
            "changed": changed,
            "unchanged": unchanged,
            "removed": removed,
        },
        "errors": errors,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    extracted, case_errors, case_file_count = extracted_sources(cases_dir)
    pending = [
        document
        for document in selected
        if document["sha256"].casefold()
        not in extracted.get(normalize_path(document["path"]), set())
    ]
    database = state_dir / "history.db"
    case_files = [*cases_dir.rglob("*.json"), *cases_dir.rglob("*.jsonl")]
    latest_case_mtime = max(
        (path.stat().st_mtime for path in case_files), default=0.0
    )
    needs_compile = (
        not database.is_file()
        or latest_case_mtime > database.stat().st_mtime
        or bool(removed)
    )

    session = {
        "schema_version": 1,
        "workspace": str(workspace),
        "state_dir": str(state_dir),
        "discovery_mode": discovery_mode,
        "document_count": len(selected),
        "case_file_count": case_file_count,
        "pending_extraction": pending,
        "removed_documents": removed,
        "needs_compile": needs_compile,
        "history_db": str(database),
        "history_db_exists": database.is_file(),
        "errors": [*errors, *case_errors],
        "next_actions": [
            *( ["extract_pending_documents"] if pending else [] ),
            *( ["compile_history"] if needs_compile and not pending else [] ),
            *( ["analyze_current_change"] if not pending else [] ),
        ],
    }
    session_path = state_dir / "session.json"
    session_path.write_text(
        json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "workspace": str(workspace),
                "state_dir": str(state_dir),
                "discovery_mode": discovery_mode,
                "documents": len(selected),
                "pending_extraction": len(pending),
                "case_files": case_file_count,
                "needs_compile": needs_compile,
                "errors": len(session["errors"]),
                "session": str(session_path),
            },
            ensure_ascii=False,
        )
    )
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
