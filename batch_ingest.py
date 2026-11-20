import os
from dotenv import load_dotenv
from supabase import create_client, Client


# Load environment configuration from variables.env
load_dotenv("variables.env")

url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_SERVICE_KEY")


if not url or not key:
    print("Error: Supabase credentials not found in variables.env")
    exit(1)


# Initialize Supabase client using Service Role key
supabase: Client = create_client(url, key)

# Sample structured batch coupon feed representing incoming corporate data
BATCH_COUPONS = [
    {
        "barcode": "CVS-OFFER-9001",
        "discount_value": 3.00,
        "min_spend": 30.00,
        "expires_at": "2026-12-31T23:59:59Z",
        "is_active": True,
        "rules": [
            {"target_category": None, "rule_type": "store_wide"}
        ]
    },
    {
        "barcode": "CVS-OFFER-9002",
        "discount_value": 5.00,
        "min_spend": 20.00,
        "expires_at": "2026-12-15T23:59:59Z",
        "is_active": True,
        "rules": [
            {"target_category": "Personal Care", "rule_type": "department_exclusive"}
        ]
    }
]


def ingest_coupon_batch(batch_data):
    """Processes batch coupon feeds, upserting parent coupons and linking rules."""
    print(f"Starting batch ingestion for {len(batch_data)} records...")

    for item in batch_data:
        # Separate the nested rules array from the main coupon metadata
        rules = item.pop("rules", [])

        try:
            # 1. Upsert parent coupon record into the 'coupons' table based on barcode uniqueness
            coupon_response = supabase.table("coupons").upsert(item, on_conflict="barcode").execute()

            if coupon_response.data:
                inserted_coupon = coupon_response.data[0]
                coupon_id = inserted_coupon['id']
                print(f"Successfully inserted coupon: {inserted_coupon['barcode']} (ID: {coupon_id})")

                # 2. Attach the generated coupon_id to each rule and insert into 'coupon_rules'
                for rule in rules:
                    rule["coupon_id"] = coupon_id
                    rule_response = supabase.table("coupon_rules").insert(rule).execute()
                    if rule_response.data:
                        print(f" -> Added rule for category: {rule.get('target_category', 'Store-wide')}")
            else:
                print(f"Failed to insert coupon {item['barcode']}")

        except Exception as e:
            print(f"Error processing coupon {item['barcode']}: {e}")

if __name__ == "__main__":
    ingest_coupon_batch(BATCH_COUPONS)
