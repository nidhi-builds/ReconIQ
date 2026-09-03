from pydantic import BaseModel

from data.schemas import LedgerEntry


class DuplicateLink(BaseModel):
    retained_index: int
    duplicate_index: int
    retained_entry: LedgerEntry
    duplicate_entry: LedgerEntry


class DeduplicationResult(BaseModel):
    retained_entries: list[LedgerEntry]
    duplicates: list[DuplicateLink]


def deduplicate_ledger(
    entries: list[LedgerEntry], window_days: int = 1
) -> DeduplicationResult:
    """Deduplicate one partition, retaining the first canonical input entry."""
    retained_entries: list[LedgerEntry] = []
    duplicates: list[DuplicateLink] = []
    retained_by_key: dict[tuple[str, float], list[tuple[int, LedgerEntry]]] = {}

    for input_index, entry in enumerate(entries):
        key = (entry.order_id, entry.amount)
        canonical = next(
            (
                candidate
                for candidate in retained_by_key.get(key, [])
                if abs((entry.order_date - candidate[1].order_date).days)
                <= window_days
            ),
            None,
        )
        if canonical is not None:
            retained_index, retained_entry = canonical
            duplicates.append(
                DuplicateLink(
                    retained_index=retained_index,
                    duplicate_index=input_index,
                    retained_entry=retained_entry,
                    duplicate_entry=entry,
                )
            )
            continue

        retained_entries.append(entry)
        retained_by_key.setdefault(key, []).append((input_index, entry))

    return DeduplicationResult(
        retained_entries=retained_entries,
        duplicates=duplicates,
    )
