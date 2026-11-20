import copy
import sys
from pathlib import Path
import pytest

# Ensure ~/cvs_project is importable when running pytest from anywhere
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, db: dict, table_name: str):
        self.db = db
        self.table_name = table_name
        self._op = "select"
        self._payload = None
        self._on_conflict = None
        self._filters = []
        self._gt_filters = []
        self._order_col = None
        self._order_desc = False
        self._limit = None

    def select(self, _columns: str):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict):
        self._op = "update"
        self._payload = payload
        return self

    def upsert(self, payload: dict, on_conflict: str | None = None):
        self._op = "upsert"
        self._payload = payload
        self._on_conflict = on_conflict
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col: str, val):
        self._filters.append((col, val))
        return self

    def gt(self, col: str, val):
        self._gt_filters.append((col, val))
        return self

    def order(self, col: str, desc: bool = False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, count: int):
        self._limit = count
        return self

    def _matches(self, row: dict) -> bool:
        for col, val in self._filters:
            if row.get(col) != val:
                return False
        for col, val in self._gt_filters:
            if row.get(col) is None or not (row.get(col) > val):
                return False
        return True

    def execute(self) -> FakeResponse:
        rows = self.db.setdefault(self.table_name, [])

        if self._op == "select":
            matched = [copy.deepcopy(r) for r in rows if self._matches(r)]
            if self._order_col:
                matched.sort(
                    key=lambda r: r.get(self._order_col),
                    reverse=self._order_desc,
                )
            if self._limit is not None:
                matched = matched[: self._limit]

            # Attach nested coupon_rules when selecting coupons with coupon_rules(*)
            if self.table_name == "coupons":
                all_rules = self.db.get("coupon_rules", [])
                for c in matched:
                    c["coupon_rules"] = [
                        copy.deepcopy(rule)
                        for rule in all_rules
                        if rule.get("coupon_id") == c.get("id")
                    ]
            return FakeResponse(matched)

        if self._op == "insert":
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            inserted = []
            for item in items:
                row = copy.deepcopy(item)
                if "id" not in row:
                    row["id"] = f"{self.table_name}-{len(rows) + len(inserted) + 1}"
                rows.append(row)
                inserted.append(copy.deepcopy(row))
            return FakeResponse(inserted)

        if self._op == "update":
            updated = []
            for row in rows:
                if self._matches(row):
                    row.update(copy.deepcopy(self._payload))
                    updated.append(copy.deepcopy(row))
            return FakeResponse(updated)

        if self._op == "upsert":
            item = copy.deepcopy(self._payload)
            conflict_col = self._on_conflict or "id"
            existing = next(
                (r for r in rows if r.get(conflict_col) == item.get(conflict_col)),
                None,
            )
            if existing:
                existing.update(item)
                return FakeResponse([copy.deepcopy(existing)])
            if "id" not in item:
                item["id"] = f"{self.table_name}-{len(rows) + 1}"
            rows.append(item)
            return FakeResponse([copy.deepcopy(item)])

        if self._op == "delete":
            remaining = []
            deleted = []
            for row in rows:
                if self._matches(row):
                    deleted.append(copy.deepcopy(row))
                else:
                    remaining.append(row)
            self.db[self.table_name] = remaining
            return FakeResponse(deleted)

        return FakeResponse([])


class FakeSupabaseClient:
    def __init__(self, initial_data: dict | None = None):
        self.db = {
            "coupons": [],
            "coupon_rules": [],
            "redemption_logs": [],
            "pipeline_state": [],
        }
        if initial_data:
            for k, v in initial_data.items():
                self.db[k] = copy.deepcopy(v)

    def table(self, table_name: str) -> FakeQuery:
        return FakeQuery(self.db, table_name)


@pytest.fixture
def fake_supabase():
    def _factory(initial_data: dict | None = None) -> FakeSupabaseClient:
        return FakeSupabaseClient(initial_data)

    return _factory
