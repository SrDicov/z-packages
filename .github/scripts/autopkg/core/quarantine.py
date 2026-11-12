"""
Quarantine and Exponential Backoff Manager.

Prevents "poison pill" packages (broken upstreams or build failures)
from repeatedly consuming CI runner quotas.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class QuarantineRecord:
    pkgname: str
    failure_count: int
    last_failed_version: str
    last_failure_time: float
    error_summary: str
    backoff_until: float


class QuarantineManager:
    # Backoff durations in seconds: 6h, 24h, 72h, 7d
    BACKOFF_STEPS = [
        6 * 3600,       # 1 failure -> 6 hours
        24 * 3600,      # 2 failures -> 1 day
        72 * 3600,      # 3 failures -> 3 days
        7 * 24 * 3600,  # 4+ failures -> 7 days
    ]

    def __init__(self, state_file: str | Path = "quarantine.json") -> None:
        self.state_file = Path(state_file).resolve()
        self.records: Dict[str, QuarantineRecord] = {}
        self.load()

    def load(self) -> None:
        if not self.state_file.is_file():
            self.records = {}
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for pkg, item in data.items():
                self.records[pkg] = QuarantineRecord(
                    pkgname=item["pkgname"],
                    failure_count=int(item["failure_count"]),
                    last_failed_version=str(item["last_failed_version"]),
                    last_failure_time=float(item["last_failure_time"]),
                    error_summary=str(item.get("error_summary", "")),
                    backoff_until=float(item.get("backoff_until", 0.0)),
                )
        except Exception:
            self.records = {}

    def save(self) -> None:
        data: Dict[str, Any] = {}
        for pkg, rec in self.records.items():
            data[pkg] = asdict(rec)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    def is_quarantined(self, pkgname: str, candidate_version: Optional[str] = None) -> bool:
        """
        Check if package is quarantined.
        If a NEW candidate_version arrives that is different from the failed version,
        quarantine is bypassed (to allow new upstream fixes to be tested).
        """
        rec = self.records.get(pkgname)
        if not rec:
            return False

        # If upstream released a newer version, give it a chance
        if candidate_version and rec.last_failed_version != candidate_version:
            return False

        now = time.time()
        return now < rec.backoff_until

    def record_failure(self, pkgname: str, failed_version: str, error_summary: str = "") -> None:
        now = time.time()
        rec = self.records.get(pkgname)

        if rec and rec.last_failed_version == failed_version:
            count = rec.failure_count + 1
        else:
            count = 1

        # Calculate backoff step
        step_idx = min(count - 1, len(self.BACKOFF_STEPS) - 1)
        backoff_delay = self.BACKOFF_STEPS[step_idx]
        backoff_until = now + backoff_delay

        self.records[pkgname] = QuarantineRecord(
            pkgname=pkgname,
            failure_count=count,
            last_failed_version=failed_version,
            last_failure_time=now,
            error_summary=error_summary[:300],  # Bound summary size
            backoff_until=backoff_until,
        )
        self.save()

    def record_success(self, pkgname: str) -> None:
        """Clear quarantine on successful build/update."""
        if pkgname in self.records:
            del self.records[pkgname]
            self.save()

    def list_quarantined(self) -> List[QuarantineRecord]:
        now = time.time()
        return [r for r in self.records.values() if now < r.backoff_until]
