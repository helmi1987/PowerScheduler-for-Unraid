#!/usr/bin/python3
"""
==============================================================================
 MASTER POWER SCRIPT - THE PLANNER (v6.3 FINAL)
==============================================================================
 Context:   Unraid Server (Lucerne, CH)
 Schedule:  Daily (e.g., 13:00) via UserScripts Plugin
 Output:    JSON Schedule for the Executor Script
 Dependency: Standard Python 3 libraries only (urllib, json, etc.)
==============================================================================
"""

import json
import datetime
import time
import urllib.request
import urllib.parse
import urllib.error
import math
import os
import sys

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================

# CKW API Endpoint (MuleSoft)
API_URL = "https://e-ckw-public-data.de-c1.eu1.cloudhub.io/api/v1/netzinformationen/energie/dynamische-preise"

# Constraints
HARD_CAP_RP = 6.0         # Threshold: Above 6.0 Rappen = BLOCKED
STORAGE_PATH = "/mnt/user/appdata/power_scheduler/"
FILENAME_FORMAT = "%Y-%m-%d.json"

# ==============================================================================
# 2. HELPER FUNCTIONS
# ==============================================================================

def get_swiss_offset_str():
    """Returns +01:00 (Winter) or +02:00 (Summer) for URL parameters."""
    is_dst = time.localtime().tm_isdst > 0
    return "+02:00" if is_dst else "+01:00"

def is_lucerne_holiday(check_date):
    """Checks for Weekends or specific Lucerne Public Holidays."""
    # 1. Weekend Check (Saturday=5, Sunday=6)
    if check_date.weekday() >= 5: 
        return True, "WEEKEND"

    # 2. Fixed Holidays (Day, Month)
    fixed_holidays = {
        (1, 1): "Neujahr", 
        (1, 2): "Berchtoldstag", 
        (5, 1): "Tag der Arbeit", 
        (8, 1): "Nationalfeiertag", 
        (8, 15): "Maria Himmelfahrt", 
        (11, 1): "Allerheiligen", 
        (12, 8): "Maria Empfaengnis", 
        (12, 25): "Weihnachten", 
        (12, 26): "Stephanstag"
    }
    
    if (check_date.month, check_date.day) in fixed_holidays:
        return True, fixed_holidays[(check_date.month, check_date.day)]
    
    # (Easter logic omitted for simplicity, Weekends cover most cases)
    return False, "WORKDAY"

def cleanup_old_files():
    """Deletes ANY schedule file older than TODAY."""
    print(f"\n[CLEANUP] Removing outdated plans...")
    today = datetime.date.today()
    count = 0
    
    if os.path.exists(STORAGE_PATH):
        for filename in os.listdir(STORAGE_PATH):
            if filename.endswith(".json"):
                try:
                    # Parse filename YYYY-MM-DD.json
                    file_date_str = filename.replace(".json", "")
                    file_date = datetime.datetime.strptime(file_date_str, "%Y-%m-%d").date()
                    
                    # Strict cleanup: If date is before today -> Delete
                    if file_date < today:
                        os.remove(os.path.join(STORAGE_PATH, filename))
                        print(f"  - Deleted: {filename}")
                        count += 1
                except ValueError:
                    continue 
    
    if count == 0:
        print("  > System clean. No old files.")
    else:
        print(f"  > Removed {count} old files.")

# ==============================================================================
# 3. API FETCHING
# ==============================================================================

def fetch_ckw_data(target_date):
    """Fetches raw JSON from CKW API using urllib."""
    # Define Time Window: 00:00:00 to 23:59:59
    start_dt = datetime.datetime.combine(target_date, datetime.time(0, 0, 0))
    end_dt = datetime.datetime.combine(target_date, datetime.time(23, 59, 59))
    
    # Construct ISO Strings with Offset
    offset = get_swiss_offset_str()
    start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S") + offset
    end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S") + offset
    
    # Build Query
    params = {
        'tariff_type': 'grid_usage',
        'tariff_name': 'home_dynamic',
        'start_timestamp': start_str,
        'end_timestamp': end_str
    }
    
    query_string = urllib.parse.urlencode(params)
    full_url = f"{API_URL}?{query_string}"
    
    print(f"[INFO] Fetching Data for: {target_date}")
    # print(f"[DEBUG] URL: {full_url}") # Uncomment if debugging needed

    try:
        req = urllib.request.Request(full_url)
        with urllib.request.urlopen(req) as response:
            if response.status != 200:
                print(f"[ERROR] HTTP Error {response.status}")
                return None
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"[ERROR] API Request Failed: {e}")
        return None

# ==============================================================================
# 4. PROCESSING LOGIC
# ==============================================================================

def process_schedule(raw_data, target_date):
    """Parses CKW JSON and calculates 1-20 Tiers."""
    if not raw_data or 'prices' not in raw_data:
        print("[ERROR] Invalid JSON format (missing 'prices').")
        return None

    valid_slots = []
    
    # 1. Parse Prices
    for item in raw_data['prices']:
        try:
            # CKW Structure: item -> grid_usage -> [ { value: X } ]
            usage_list = item.get('grid_usage', [])
            price_val = 0.0
            
            if usage_list and len(usage_list) > 0:
                raw_val = usage_list[0].get('value')
                if raw_val is not None:
                    # Convert CHF to Rappen (e.g. 0.05 CHF -> 5.0 Rp)
                    price_val = float(raw_val) * 100
            
            ts = item.get('start_timestamp')
            
            if ts:
                valid_slots.append({'ts': ts, 'price': price_val})
                
        except Exception as e:
            continue

    if not valid_slots:
        print("[ERROR] No valid price slots extracted.")
        return None

    # 2. Calculate Tiers
    # Filter valid prices (<= Hard Cap) for percentile calculation
    prices_below_cap = [s['price'] for s in valid_slots if s['price'] <= HARD_CAP_RP]
    prices_below_cap.sort()
    total_valid = len(prices_below_cap)
    
    timeline = {}
    
    for slot in valid_slots:
        price = slot['price']
        ts = slot['ts']
        
        # Determine Status
        if price > HARD_CAP_RP:
            status = "BLOCKED"
            tier = 99
        else:
            status = "ALLOWED"
            # Calculate Dynamic Tier (1-20)
            if total_valid > 0:
                try:
                    rank = prices_below_cap.index(price)
                    percentile = (rank / total_valid) * 100
                    # Map 0-100% to Tier 1-20
                    tier = math.floor(percentile / 5) + 1
                    if tier > 20: tier = 20
                except ValueError:
                    tier = 20 # Should not happen
            else:
                tier = 99 # Fallback
        
        timeline[ts] = {
            "price_rp": round(price, 4),
            "tier": tier,
            "status": status
        }

    # 3. Calendar Context
    is_offpeak, reason = is_lucerne_holiday(target_date)

    return {
        "metadata": {
            "target_date": str(target_date),
            "generated_at": datetime.datetime.now().isoformat(),
            "profile_mode": "WEEKEND" if is_offpeak else "STANDARD",
            "calendar_reason": reason,
            "hard_cap_rp": HARD_CAP_RP
        },
        "timeline": timeline
    }

# ==============================================================================
# 5. MAIN EXECUTION
# ==============================================================================

def main():
    print("--- UNRAID POWER PLANNER (v6.3 Final) ---")
    
    # 1. Ensure Directory
    if not os.path.exists(STORAGE_PATH):
        try:
            os.makedirs(STORAGE_PATH)
            print(f"[INIT] Created directory: {STORAGE_PATH}")
        except Exception as e:
            print(f"[FATAL] Could not create directory: {e}")
            sys.exit(1)

    # 2. Cleanup Old Files
    cleanup_old_files()

    # 3. Define Targets (Today + Tomorrow)
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    
    # Always generate Tomorrow.
    targets = [tomorrow]
    
    # Check if Today exists. If not, generate it too (Self-Healing).
    today_file = os.path.join(STORAGE_PATH, today.strftime(FILENAME_FORMAT))
    if not os.path.exists(today_file):
        print(f"[WARN] Plan for TODAY ({today}) is missing. Adding to queue.")
        targets.insert(0, today)
    
    # 4. Processing Loop
    for target_date in targets:
        # Fetch
        data = fetch_ckw_data(target_date)
        
        if data:
            # Process
            schedule = process_schedule(data, target_date)
            
            if schedule:
                # Save
                fpath = os.path.join(STORAGE_PATH, target_date.strftime(FILENAME_FORMAT))
                try:
                    with open(fpath, 'w') as f:
                        json.dump(schedule, f, indent=4)
                    print(f"[SUCCESS] Schedule saved: {fpath}")
                    
                    # Preview
                    meta = schedule['metadata']
                    print(f"  > Profile: {meta['profile_mode']} ({meta['calendar_reason']})")
                    print(f"  > Hard Cap: {meta['hard_cap_rp']} Rp.")
                    
                except Exception as e:
                    print(f"[ERROR] Could not write file: {e}")
            else:
                print(f"[FAIL] Processing failed for {target_date}")
        else:
            print(f"[FAIL] API fetch failed for {target_date}")

    print("--- FINISHED ---")

if __name__ == "__main__":
    main()
