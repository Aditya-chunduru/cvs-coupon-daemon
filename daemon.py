import json
import hashlib
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
import schedule
from supabase import Client, create_client
from dotenv import load_dotenv


load_dotenv("variables.env") # Automatically loads variables from your .env file

# Global flag to control the main daemon loop
running = True

# 1. DEFINE VARIABLES FIRST
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

worker_id = "cvs-coupon-sync-daemon"

# Safety check: Warn if credentials are missing
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("WARNING: SUPABASE_URL or SUPABASE_SERVICE_KEY not found in environment variables!")



def handle_sigterm(signum, frame):
  """Signal handler for SIGTERM and SIGINT.

  When systemd stops the service or you press Ctrl+C, this catches the signal
  and breaks the loop cleanly so the daemon can shut down gracefully.
  """
  global running
  logger.info("Received stop signal, shutting down daemon gracefully.")
  running = False


class JSONFormatter(logging.Formatter):
  """Custom logging formatter that outputs log records as structured JSON strings.

  This makes logs machine-readable and easy to ingest into log aggregators
  or filter directly via journalctl.
  """

  def format(self, record):
    log_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }
    # Include exception tracebacks automatically if an error occurs
    if record.exc_info:
      log_record["exception"] = self.formatException(record.exc_info)
    return json.dumps(log_record)


def setup_logger():
  """Configures the root logger to output JSON-formatted logs to stdout,

  which systemd and journald automatically capture.
  """
  log = logging.getLogger("cvs_coupon_daemon")
  log.setLevel(logging.INFO)

  # StreamHandler prints to stdout
  handler = logging.StreamHandler(sys.stdout)
  handler.setFormatter(JSONFormatter())

  if not log.handlers:
    log.addHandler(handler)

  return log


# Initialize the logger instance
logger = setup_logger()

# Register signal hooks for graceful shutdowns
signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

def init_supabase_client():
    """Safely initializes and returns the Supabase client."""
    try:
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            logger.info("Supabase client initialized successfully.")
            return client
        else:
            logger.error("Cannot initialize Supabase client: Missing credentials in environment.")
            return None
    except Exception as e:
        logger.exception(f"Failed to initialize Supabase client: {e}")
        return None

def compute_event_hash(record: dict) -> str:
    """Generate a deterministic SHA-256 hash matching the POS ingestion contract."""
    raw_string = f"{record.get('store_id')}-{record.get('register_id')}-{record.get('sequence_num')}"
    return hashlib.sha256(raw_string.encode("utf-8")).hexdigest()


def sync_coupon_catalog(supabase: Client, coupon_batch: list[dict]) -> int:
    """Upsert master coupons on barcode conflict and sync child coupon_rules."""
    synced_count = 0

    for item in coupon_batch:
        if not running:
            logger.warning("Shutdown flag detected; halting catalog batch early.")
            break

        rules = item.pop("rules", [])

        coupon_res = (
            supabase.table("coupons")
            .upsert(item, on_conflict="barcode")
            .execute()
        )

        if not coupon_res.data:
            continue

        coupon_id = coupon_res.data[0]["id"]

        if rules:
            supabase.table("coupon_rules").delete().eq(
                "coupon_id", coupon_id
            ).execute()

            rule_rows = [
                {
                    "coupon_id": coupon_id,
                    "target_category": r.get("target_category"),
                    "rule_type": r.get("rule_type"),
                }
                for r in rules
            ]
            supabase.table("coupon_rules").insert(rule_rows).execute()

        synced_count += 1

    return synced_count


def validate_coupon_rules(supabase: Client, log_entry: dict) -> tuple[bool, str]:
    """
    Validates business rules for a redemption attempt:
    - Expiration date check
    - Minimum item purchase threshold
    - ExtraCare card eligibility
    """
    coupon_id = log_entry.get("coupon_id")
    if not coupon_id:
        return False, "Missing coupon_id in log entry"

    # Fetch coupon and its rules children
    res = (
        supabase.table("coupons")
        .select("*, coupon_rules(*)")
        .eq("id", coupon_id)
        .execute()
    )

    if not res.data:
        return False, "Coupon not found in catalog"

    coupon = res.data[0]
    now_utc = datetime.now(timezone.utc)

    # 1. Active flag & expiration check
    if coupon.get("is_active") is False:
        return False, "Coupon is inactive"

    # 1. Expiration check
    expires_at = coupon.get("expires_at")
    if expires_at:
        exp_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        if now_utc > exp_dt:
            return False, "Coupon has expired"


    # Parse items_purchased jsonb safely
    raw_items = log_entry.get("items_purchased")
    if isinstance(raw_items, list):
        actual_items = len(raw_items)
    elif isinstance(raw_items, dict):
        actual_items = int(raw_items.get("count") or len(raw_items.get("items") or []))
    elif isinstance(raw_items, (int, float)):
        actual_items = int(raw_items)
    else:
        actual_items = 0

    card_number = (log_entry.get("card_number") or "").strip()
    rules = coupon.get("coupon_rules") or []
    

    # 2. Check rule constraints if rules are defined
    for rule in rules:
        rule_type = (rule.get("rule_type") or "").strip()
        
        if rule_type.startswith("min_items"):
            # Supports either rule["min_items"], rule["target_value"], or "min_items:N"
            if ":" in rule_type:
                required_min = int(rule_type.split(":", 1)[1])
            else:
                required_min = int(rule.get("min_items") or rule.get("target_value") or 0)

            if actual_items < required_min:
                return (
                    False,
                    f"Minimum items requirement not met (Required: {required_min}, Got: {actual_items})",
                )

        elif rule_type == "extracare_required":
            if not card_number or len(card_number) != 16:
                return False, "ExtraCare card eligibility failed: valid 16-digit card_number required"
         

    return True, "Valid"





def run_coupon_sync_job(supabase: Client):
    """Core scheduled job: stateful polling, batch verification, and checkpoint commit."""
    logger.info("Executing scheduled task: Checking coupon pipeline state...")
    if not supabase:
        logger.warning("Supabase client unavailable. Skipping database poll.")
        return

    last_seq = 0

    try:
        # 1. Fetch current high-water mark from pipeline_state
        state_res = (
            supabase.table("pipeline_state")
            .select("*")
            .eq("worker_id", worker_id)
            .execute()
        )

        if state_res.data:
            state_row = state_res.data[0]
            last_seq = state_row.get("last_sequence_num", 0)
            if state_row.get("last_status") == "paused":
                logger.info("Pipeline is paused in pipeline_state; skipping tick.")
                return
        else:
            supabase.table("pipeline_state").insert(
                {
                    "worker_id": worker_id,
                    "last_sequence_num": 0,
                    "last_status": "running",
                }
            ).execute()

        # Mark worker active
        supabase.table("pipeline_state").update(
            {
                "last_status": "running",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("worker_id", worker_id).execute()

        # 2. Poll new redemption_logs strictly above the high-water mark
        logs_res = (
            supabase.table("redemption_logs")
            .select("*")
            .gt("sequence_num", last_seq)
            .order("sequence_num", desc=False)
            .limit(100)
            .execute()
        )

        new_logs = logs_res.data or []
        max_seq_processed = last_seq
        verified_count = 0

        for log_entry in new_logs:
            if not running:
                logger.warning("Shutdown signal received during log batch processing.")
                break

            expected_hash = compute_event_hash(log_entry)
            if log_entry.get("event_hash") != expected_hash:
                logger.error(
                    f"Hash mismatch on sequence_num={log_entry.get('sequence_num')}; flagging failed."
                )
                supabase.table("redemption_logs").update({"status": "failed"}).eq(
                    "id", log_entry["id"]
                ).execute()
                max_seq_processed = max(max_seq_processed, log_entry["sequence_num"])
                continue

            if log_entry.get("status") == "pending":
                # Enforce business rules before marking redeemed
                is_valid, reason = validate_coupon_rules(supabase, log_entry)
                
                if is_valid:
                    supabase.table("redemption_logs").update({"status": "redeemed"}).eq(
                        "id", log_entry["id"]
                    ).execute()
                else:
                    logger.warning(
                        f"Coupon validation failed for log id={log_entry['id']}: {reason}. Marking as rejected."
                    )
                    supabase.table("redemption_logs").update({"status": "rejected"}).eq("id", log_entry["id"]).execute()

            max_seq_processed = max(max_seq_processed, log_entry["sequence_num"])
            verified_count += 1

        # 3. Commit new high-water mark & set worker status back to idle
        supabase.table("pipeline_state").update(
            {
                "last_sequence_num": max_seq_processed,
                "last_status": "idle",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("worker_id", worker_id).execute()

        logger.info(
            f"Sync job complete: processed={verified_count}, high_water_mark={max_seq_processed}"
        )

    except Exception as e:
        logger.exception(f"Error executing scheduled database job: {e}")
        try:
            supabase.table("pipeline_state").update(
                {
                    "last_status": "error",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("worker_id", worker_id).execute()
        except Exception as state_exc:
            logger.error(f"Failed to update pipeline_state to error: {state_exc}")


if __name__ == "__main__":
    logger.info("CVS coupon daemon started with Supabase & Scheduler integration.")

    db_client = init_supabase_client()

    # Register recurring jobs
    schedule.every(5).minutes.do(run_coupon_sync_job, supabase=db_client)
    schedule.every(60).seconds.do(
        lambda: logger.info("Daemon heartbeat: active and waiting for tasks.")
    )

    # Execute an immediate initial sync on startup before entering the loop
    run_coupon_sync_job(db_client)

    while running:
        schedule.run_pending()
        time.sleep(1)

    logger.info("CVS coupon daemon stopped cleanly.")
