"""
Replayable reference data.

The destinations and institutions in ``backend/data/reference/*.csv`` are real records with
real official sources, not demo content. They must survive a reseed, which means they cannot
live in ``seed_data.py`` (rebuilt from scratch each time) and cannot exist only as a one-off
import somebody ran by hand.

So they are kept as versioned CSV files and replayed through the ordinary import pipeline -
the same staging, diffing, conflict detection and provenance writing an operator gets in the
admin UI. Running it twice is safe: the second pass diffs to UNCHANGED and commits nothing.

    python3 -m app.services.reference_data          # load everything
    ./scripts/load-reference-data.sh                # same, with the stack's env

Order matters: countries must exist before universities can resolve their `country` column.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.services import importer

log = logging.getLogger(__name__)

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"

# (entity type, filename, default source tier). Dependencies first.
MANIFEST: List[tuple[str, str, str]] = [
    ("country", "countries-2026-10.csv", "TIER_3_OFFICIAL_ADMISSIONS_PORTAL"),
    ("university", "universities-2026-10.csv", "TIER_1_OFFICIAL_UNIVERSITY"),
    ("university", "universities-japan-finland.csv", "TIER_1_OFFICIAL_UNIVERSITY"),
    ("university", "universities-germany.csv", "TIER_1_OFFICIAL_UNIVERSITY"),
    ("university", "universities-batch2.csv", "TIER_1_OFFICIAL_UNIVERSITY"),
    ("university", "universities-uk-us.csv", "TIER_1_OFFICIAL_UNIVERSITY"),
    ("program", "programmes-2026-10.csv", "TIER_1_OFFICIAL_UNIVERSITY"),
    ("program_requirement", "programme-requirements-2026-10.csv", "TIER_1_OFFICIAL_UNIVERSITY"),
    ("program_intake", "programme-intakes-2026-10.csv", "TIER_1_OFFICIAL_UNIVERSITY"),
    ("scholarship", "scholarships-2026-10.csv", "TIER_4_OFFICIAL_SCHOLARSHIP_PROVIDER"),
]


def available() -> List[Dict[str, Any]]:
    out = []
    for entity_type, filename, tier in MANIFEST:
        path = REFERENCE_DIR / filename
        out.append({"entity_type": entity_type, "filename": filename,
                    "source_type": tier, "exists": path.exists(),
                    "path": str(path)})
    return out


def load_all(db: Session, actor: str = "reference-data-loader",
             dry_run: bool = False) -> Dict[str, Any]:
    """Stage and commit every manifest file. Returns a per-file summary."""
    summary: Dict[str, Any] = {"files": [], "created": 0, "updated": 0,
                               "unchanged": 0, "conflicts": 0, "errors": 0}

    for entity_type, filename, tier in MANIFEST:
        path = REFERENCE_DIR / filename
        if not path.exists():
            log.warning("reference data: %s is missing, skipping", path)
            summary["files"].append({"filename": filename, "skip_reason": "file not found"})
            continue

        rows = importer.parse_payload(path.read_text(encoding="utf-8"), filename)
        batch = importer.stage(db, entity_type=entity_type, rows=rows, filename=filename,
                               source_url="", source_type=tier,
                               notes=f"Reference data replay: {filename}", actor=actor)

        entry: Dict[str, Any] = {
            "filename": filename, "entity_type": entity_type, "reference": batch.reference,
            "rows": batch.row_count, "create": batch.created_rows,
            "update": batch.updated_rows, "conflict": batch.conflict_rows,
            "skipped": batch.skipped_rows,
        }
        bad = [f"line {r.line_number}: {'; '.join(r.errors)}"
               for r in batch.rows if r.errors]
        if bad:
            entry["errors"] = bad
            summary["errors"] += len(bad)

        if dry_run:
            batch.status = "DISCARDED"
            db.commit()
            entry["committed"] = False
            summary["created"] += batch.created_rows
            summary["updated"] += batch.updated_rows
        else:
            result = importer.commit_batch(db, batch, actor=actor)
            entry["committed"] = True
            entry["provenance_records"] = result["verifications"]

            # Report what the database actually holds, not what staging intended. These
            # differ whenever a row fails at commit, and reporting the intent is how a
            # load once announced "4 created, 0 errors" with two of them missing.
            entry["create"] = result["created"]
            entry["update"] = result["updated"]
            summary["created"] += result["created"]
            summary["updated"] += result["updated"]

            if result["failures"]:
                entry["failures"] = result["failures"]
                entry.setdefault("errors", []).extend(result["failures"])
                summary["errors"] += len(result["failures"])

        summary["unchanged"] += batch.skipped_rows
        summary["conflicts"] += batch.conflict_rows
        summary["files"].append(entry)

    return summary


def main() -> None:  # pragma: no cover - operator entry point
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from app.core.database import SessionLocal

    dry = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}
    with SessionLocal() as db:
        result = load_all(db, dry_run=dry)

    print(f"\nReference data {'(dry run) ' if dry else ''}loaded from {REFERENCE_DIR}")
    for entry in result["files"]:
        if entry.get("skip_reason"):
            print(f"  {entry['filename']}: {entry['skip_reason']}")
            continue
        print(f"  {entry['filename']}: {entry['rows']} rows -> "
              f"{entry['create']} new, {entry['update']} updated, "
              f"{entry['conflict']} conflicts, {entry['skipped']} unchanged"
              + (f", {entry.get('provenance_records', 0)} provenance records"
                 if entry.get("committed") else ""))
        for problem in entry.get("errors", []):
            print(f"    ! {problem}")
    print(f"  total: {result['created']} created, {result['updated']} updated, "
          f"{result['conflicts']} conflicts, {result['errors']} errors")
    if result["conflicts"]:
        print("  Conflicts were staged but not applied. Resolve them in "
              "Admin > Data import & verification.")


if __name__ == "__main__":  # pragma: no cover
    main()
