import os
import random
import hashlib
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables from your server's env file
load_dotenv("variables.env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Quick debug print to verify they are loaded
print(f"DEBUG URL Loaded: {SUPABASE_URL is not None}")
print(f"DEBUG KEY Loaded: {SUPABASE_SERVICE_KEY is not None}")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in environment variables!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def simulate_pos_transaction():
    # Generate a strict 4-digit zero-padded transaction ID (e.g., "0482")
    transaction_id = f"{random.randint(0, 9999):04d}"

    # 16-character card number matching your length check constraint
    card_number = "".join([str(random.randint(0, 9)) for _ in range(16)])

    # Define variables first so they are available for the hash
    coupon_id = "1f8b324d-6399-4699-802c-6f2ae84d49b0"
    scanned_at = datetime.now(timezone.utc).isoformat()
    store_id = 1293
    register_id = 18

    # Query the highest existing sequence_num so every simulated scan increments monotonically
    max_res = (
        supabase.table("redemption_logs")
        .select("sequence_num")
        .order("sequence_num", desc=True)
        .limit(1)
        .execute()
    )
    current_max = max_res.data[0]["sequence_num"] if max_res.data else 0
    sequence_num = current_max + 1

    # Generate deterministic SHA-256 hash matching daemon.py
    raw_string = f"{store_id}-{register_id}-{sequence_num}"
    event_hash = hashlib.sha256(raw_string.encode("utf-8")).hexdigest()

    transaction_data = {
        "transaction_id": transaction_id,
        "card_number": card_number,
        "store_id": store_id,
        "register_id": register_id,
        "coupon_id": coupon_id,
        "status": "pending",
        "scanned_at": scanned_at,
        "event_hash": event_hash,
        "sequence_num": sequence_num,
        "items_purchased": random.randint(1, 5),
        "total_saved": round(random.uniform(1.00, 15.00), 2),
    }

    print(f"Simulating POS transaction {transaction_id} (seq={sequence_num}) for card ending in {card_number[-4:]}...")

    try:
        response = supabase.table("redemption_logs").insert(transaction_data).execute()
        print("POS Transaction Simulated & Logged Successfully!")
        print(response)
    except Exception as e:
        print("Error inserting transaction log:")
        print(e)

if __name__ == "__main__":
    simulate_pos_transaction()
