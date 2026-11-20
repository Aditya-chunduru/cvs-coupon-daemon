import hashlib
from datetime import datetime, timedelta, timezone
import daemon

def test_compute_event_hash_deterministic_and_sensitive():
    base = {"store_id": 101, "register_id": 1, "sequence_num": 9774}
    expected = hashlib.sha256(b"101-1-9774").hexdigest()

    assert daemon.compute_event_hash(base) == expected
    assert len(daemon.compute_event_hash(base)) == 64

    assert daemon.compute_event_hash({**base, "store_id": 102}) != expected
    assert daemon.compute_event_hash({**base, "register_id": 2}) != expected
    assert daemon.compute_event_hash({**base, "sequence_num": 9775}) != expected


def test_validate_coupon_rules_valid_coupon(fake_supabase):
    future_iso = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    client = fake_supabase(
        {
            "coupons": [
                {
                    "id": "coup-1",
                    "barcode": "BC-100",
                    "is_active": True,
                    "expires_at": future_iso,
                }
            ],
            "coupon_rules": [
                {"id": "r-1", "coupon_id": "coup-1", "rule_type": "min_items:2"},
                {"id": "r-2", "coupon_id": "coup-1", "rule_type": "extracare_required"},
            ],
        }
    )

    log_entry = {
        "coupon_id": "coup-1",
        "card_number": "9999000011112222",
        "items_purchased": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 1}],
    }

    ok, reason = daemon.validate_coupon_rules(client, log_entry)
    assert ok is True
    assert reason == "Valid"


def test_validate_coupon_rules_rejects_expired_and_inactive(fake_supabase):
    past_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future_iso = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    client = fake_supabase(
        {
            "coupons": [
                {
                    "id": "coup-expired",
                    "barcode": "BC-EXP",
                    "is_active": True,
                    "expires_at": past_iso,
                },
                {
                    "id": "coup-inactive",
                    "barcode": "BC-INACT",
                    "is_active": False,
                    "expires_at": future_iso,
                },
            ]
        }
    )

    ok_exp, reason_exp = daemon.validate_coupon_rules(client, {"coupon_id": "coup-expired"})
    assert ok_exp is False
    assert "expired" in reason_exp.lower()

    ok_inact, reason_inact = daemon.validate_coupon_rules(client, {"coupon_id": "coup-inactive"})
    assert ok_inact is False
    assert "inactive" in reason_inact.lower()


def test_validate_coupon_rules_min_items_and_extracare(fake_supabase):
    future_iso = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    client = fake_supabase(
        {
            "coupons": [
                {
                    "id": "coup-rules",
                    "barcode": "BC-RULES",
                    "is_active": True,
                    "expires_at": future_iso,
                }
            ],
            "coupon_rules": [
                {"id": "r-1", "coupon_id": "coup-rules", "rule_type": "min_items:3"},
                {"id": "r-2", "coupon_id": "coup-rules", "rule_type": "extracare_required"},
            ],
        }
    )

    # Only 1 item in jsonb list -> fails min_items:3
    ok_items, _ = daemon.validate_coupon_rules(
        client,
        {
            "coupon_id": "coup-rules",
            "card_number": "9999000011112222",
            "items_purchased": [{"sku": "123456", "qty": 1}],
        },
    )
    assert ok_items is False

    # 3 items in jsonb list, but short card number -> fails extracare_required
    ok_card, _ = daemon.validate_coupon_rules(
        client,
        {
            "coupon_id": "coup-rules",
            "card_number": "12345",
            "items_purchased": [{"sku": "1"}, {"sku": "2"}, {"sku": "3"}],
        },
    )
    assert ok_card is False
