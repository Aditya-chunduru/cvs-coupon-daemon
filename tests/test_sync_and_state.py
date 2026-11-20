from datetime import datetime, timedelta, timezone
import daemon


def test_sync_coupon_catalog_upserts_and_replaces_rules(fake_supabase):
    client = fake_supabase(
        {
            "coupons": [{"id": "existing-id", "barcode": "BC-1", "is_active": False}],
            "coupon_rules": [{"id": "old-rule", "coupon_id": "existing-id", "rule_type": "old_rule"}],
        }
    )

    batch = [
        {
            "barcode": "BC-1",
            "is_active": True,
            "rules": [{"target_category": "grocery", "rule_type": "min_items:2"}],
        }
    ]

    synced = daemon.sync_coupon_catalog(client, batch)
    assert synced == 1
    assert len(client.db["coupons"]) == 1
    assert client.db["coupons"][0]["is_active"] is True
    assert len(client.db["coupon_rules"]) == 1
    assert client.db["coupon_rules"][0]["rule_type"] == "min_items:2"


def test_run_coupon_sync_job_skips_when_paused(fake_supabase, monkeypatch):
    monkeypatch.setattr(daemon, "running", True)
    client = fake_supabase(
        {
            "pipeline_state": [
                {
                    "worker_id": daemon.worker_id,
                    "last_sequence_num": 9776,
                    "last_status": "paused",
                }
            ],
            "redemption_logs": [
                {
                    "id": "log-9777",
                    "sequence_num": 9777,
                    "store_id": 101,
                    "register_id": 1,
                    "coupon_id": "coup-1",
                    "event_hash": daemon.compute_event_hash(
                        {"store_id": 101, "register_id": 1, "sequence_num": 9777}
                    ),
                    "status": "pending",
                }
            ],
        }
    )

    daemon.run_coupon_sync_job(client)

    assert client.db["pipeline_state"][0]["last_sequence_num"] == 9776
    assert client.db["redemption_logs"][0]["status"] == "pending"


def test_run_coupon_sync_job_transitions_and_checkpoint_recovery(fake_supabase, monkeypatch):
    monkeypatch.setattr(daemon, "running", True)
    future_iso = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    past_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    valid_9777 = {"store_id": 101, "register_id": 1, "sequence_num": 9777}
    valid_9778 = {"store_id": 101, "register_id": 1, "sequence_num": 9778}
    valid_9779 = {"store_id": 101, "register_id": 1, "sequence_num": 9779}

    client = fake_supabase(
        {
            "pipeline_state": [
                {
                    "worker_id": daemon.worker_id,
                    "last_sequence_num": 9776,
                    "last_status": "idle",
                }
            ],
            "coupons": [
                {"id": "coup-valid", "barcode": "BC-VAL", "is_active": True, "expires_at": future_iso},
                {"id": "coup-exp", "barcode": "BC-EXP", "is_active": True, "expires_at": past_iso},
            ],
            "coupon_rules": [],
            "redemption_logs": [
                # Should be ignored because sequence_num <= 9776
                {
                    "id": "log-old",
                    "sequence_num": 9770,
                    "store_id": 101,
                    "register_id": 1,
                    "coupon_id": "coup-valid",
                    "event_hash": "bad-hash",
                    "status": "pending",
                },
                # Valid hash + valid coupon -> redeemed
                {
                    "id": "log-9777",
                    **valid_9777,
                    "coupon_id": "coup-valid",
                    "event_hash": daemon.compute_event_hash(valid_9777),
                    "status": "pending",
                },
                # Tampered hash -> failed
                {
                    "id": "log-9778",
                    **valid_9778,
                    "coupon_id": "coup-valid",
                    "event_hash": "0" * 64,
                    "status": "pending",
                },
                # Valid hash + expired coupon -> rejected
                {
                    "id": "log-9779",
                    **valid_9779,
                    "coupon_id": "coup-exp",
                    "event_hash": daemon.compute_event_hash(valid_9779),
                    "status": "pending",
                },
            ],
        }
    )

    daemon.run_coupon_sync_job(client)

    logs_by_id = {row["id"]: row for row in client.db["redemption_logs"]}
    assert logs_by_id["log-old"]["status"] == "pending"
    assert logs_by_id["log-9777"]["status"] == "redeemed"
    assert logs_by_id["log-9778"]["status"] == "failed"
    assert logs_by_id["log-9779"]["status"] == "rejected"

    state = client.db["pipeline_state"][0]
    assert state["last_sequence_num"] == 9779
    assert state["last_status"] == "idle"
