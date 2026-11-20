import os
import hashlib
from dotenv import load_dotenv
from supabase import create_client, Client

# ==========================================
# 1. Environment & Client Initialization
# ==========================================
# Load environment variables from the local variables.env configuration file
load_dotenv("variables.env")

url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_SERVICE_KEY")

# Verify that credentials were loaded properly before proceeding
if not url or not key:
    print("Error: Supabase credentials not found in variables.env")
    exit(1)

# Initialize the Supabase client using the Service Role key to bypass RLS
supabase: Client = create_client(url, key)

# ==========================================
# 2. Stateful High-Water Mark Retrieval
# ==========================================

def get_last_sequence():
    """Retrieve the last processed sequence number from pipeline_state."""
    try:
	# Query pipeline_state specifically for worker 'daemon-01' using .eq()
        response = supabase.table("pipeline_state").select("last_sequence_num").eq("worker_id", "daemon-01").execute()

	# If a valid state record exists, return the sequence number
        if response.data:
            return response.data[0]['last_sequence_num']
        return 0
    except Exception as e:
        print(f"Error fetching pipeline state: {e}")
        return None







# ==========================================
# 3. Cryptographic Deduplication Logic
# ==========================================
def generate_event_hash(store_id, register_id, sequence_num):
    """Create a cryptographic hash to prevent duplicate entries."""
    raw_string = f"{store_id}-{register_id}-{sequence_num}"
    return hashlib.sha256(raw_string.encode('utf-8')).hexdigest()



# ==========================================
# 4. Main Execution Flow
# ==========================================
if __name__ == "__main__":
    print("Initializing CVS Coupon Ingestion Daemon...")
    # Fetch the current high-water mark sequence from the database
    last_seq = get_last_sequence()


    if last_seq is not None:
        print(f"Current high-water mark sequence: {last_seq}")

	# Simulate generating a unique event hash for the upcoming sequence item
        test_hash = generate_event_hash("Store-1234", "Reg-02", last_seq + 1)
        print(f"Generated event hash for next sequence: {test_hash}")
    else:
        print("Failed to initialize pipeline state.")
