#!/usr/bin/python3
"""
==============================================================================
 MASTER POWER SCRIPT - THE EXECUTOR (v9.0 - DURATION AWARE)
==============================================================================
"""

import json
import datetime
import time
import os
import subprocess
import sys
import statistics
import math
import fcntl

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================

OVERRIDE_NOW = "" 
DRY_RUN = True
PLANNER_PATH = "/mnt/user/appdata/power_scheduler/"
LOCK_FILE_PATH = "/tmp/power_executor.lock"

if DRY_RUN: STATE_FILE = os.path.join(PLANNER_PATH, "executor_state_dryrun.json")
else: STATE_FILE = os.path.join(PLANNER_PATH, "executor_state.json")

# EMERGENCY
DISK_PATH_CHECK = "/mnt/cache"
DISK_FULL_THRESHOLD = 90
EMERGENCY_COMMAND = ""

# --- DYNAMIC TIME PROFILES ---
TIME_PROFILES = {
    "STRICT": {
        "STANDARD": [18, 19, 20, 21, 22],
        "WEEKEND":  [12, 13, 18, 19, 20, 21, 22]
    },
    "IGNORE_TIME": { "STANDARD": [], "WEEKEND": [] },
    "NIGHT_ONLY": {
        "STANDARD": [7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23],
        "WEEKEND":  [7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23]
    }
}

# --- SCRIPTS CONFIG ---
SCRIPTS_CONFIG = [
    {
        "id": "Emby_Cache",
        "command": "/usr/bin/python3 /mnt/user/system/scripts/embycache_v2/embycache_run.py",
        "initial_runtime_min": 30,
        "min_interval_hours": 20,
        "max_interval_hours": 28,
        "max_tier": 5,
        "profile_mode": "IGNORE_TIME", # Nutzt IGNORE_TIME Definition oben
        "group": "media",
        "order": 1
    }
]

# ==============================================================================
# 2. HELPER FUNCTIONS
# ==============================================================================

def prevent_double_execution():
    global lock_file_handle
    lock_file_handle = open(LOCK_FILE_PATH, 'w')
    try: fcntl.lockf(lock_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError: sys.exit(0)

def log_debug(msg):
    if DRY_RUN: print(f"[DEBUG] {msg}")

def get_current_time():
    if OVERRIDE_NOW:
        try: return datetime.datetime.strptime(OVERRIDE_NOW, "%Y-%m-%d %H:%M")
        except: pass
    return datetime.datetime.now()

def load_state():
    if os.path.exists(STATE_FILE):
        try: return json.load(open(STATE_FILE))
        except: return {}
    return {}

def save_state(state):
    try: json.dump(state, open(STATE_FILE, 'w'), indent=4)
    except: pass

def update_runtime_stats(script_id, duration_sec):
    state = load_state()
    if script_id not in state: 
        state[script_id] = {"history": [], "avg_runtime_sec": 0, "last_run": None}
    history = state[script_id]["history"]
    history.append(duration_sec)
    if len(history) > 30: history = history[-30:]
    avg_sec = statistics.mean(history) if history else duration_sec
    state[script_id]["history"] = history
    state[script_id]["avg_runtime_sec"] = round(avg_sec, 2)
    state[script_id]["last_run"] = get_current_time().isoformat()
    save_state(state)
    if DRY_RUN: print(f"   [DRY-STATE] {script_id}: Stats updated.")
    else: print(f"   [LEARN] {script_id}: Finished in {int(duration_sec/60)}m. New Avg: {int(avg_sec/60)}m.")

def is_disk_full():
    try:
        usage = os.statvfs(DISK_PATH_CHECK)
        return ((1 - (usage.f_bavail / usage.f_blocks)) * 100) > DISK_FULL_THRESHOLD
    except: return False

def get_day_type(now):
    fpath = os.path.join(PLANNER_PATH, f"{now.strftime('%Y-%m-%d')}.json")
    if os.path.exists(fpath):
        try:
            data = json.load(open(fpath))
            return data.get('metadata', {}).get('profile_mode', 'STANDARD')
        except: pass
    return 'STANDARD'

def check_profile_blocker(script_conf, now):
    mode_name = script_conf.get("profile_mode", "IGNORE_TIME")
    day_type = get_day_type(now)
    profile_def = TIME_PROFILES.get(mode_name)
    if not profile_def: return False
    blocked_hours = profile_def.get(day_type, [])
    if now.hour in blocked_hours:
        log_debug(f"Profile '{mode_name}' blocks hour {now.hour} ({day_type}).")
        return True
    return False

# ==============================================================================
# 3. CORE LOGIC (DURATION AWARE)
# ==============================================================================

def load_full_timeline(current_time):
    combined = {}
    dates = [current_time.date(), current_time.date() + datetime.timedelta(days=1), current_time.date() + datetime.timedelta(days=2)]
    for d in dates:
        fpath = os.path.join(PLANNER_PATH, f"{d.strftime('%Y-%m-%d')}.json")
        if os.path.exists(fpath):
            try: combined.update(json.load(open(fpath)).get('timeline', {}))
            except: continue
    return combined

def parse_iso_key(ts_str):
    try:
        clean = ts_str.split('+')[0].replace('T', ' ')
        if len(clean) == 16: clean += ":00"
        return datetime.datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    except: return None

def get_avg_price_for_duration(start_dt, duration_min, timeline):
    """Calculates average price from start_dt over duration."""
    total_price = 0
    slots_count = 0
    
    # We check every 15 min slot
    steps = int(math.ceil(duration_min / 15))
    if steps < 1: steps = 1

    for i in range(steps):
        check_time = start_dt + datetime.timedelta(minutes=(i * 15))
        # Find closest slot in timeline (exact match)
        # Format matching the JSON keys
        # Note: Timeline keys are ISO strings. We need to find the matching one.
        # Efficient way: Assume timeline is dense or handle missing.
        
        # Simple lookup:
        # We need to construct the key or iterate.
        # Since exact ISO string matching is hard with seconds, we use the parsed timeline logic
        # passed into this function would be inefficient to parse every time.
        # -> Optimized: The timeline passed to this function should be a Dict of DT objects?
        # No, for simplicity we iterate or fuzzy match.
        
        # Let's find the slot in the timeline dict
        # Key format in timeline: "2024-05-20T13:00:00+02:00"
        
        found = False
        for ts, data in timeline.items():
            dt = parse_iso_key(ts)
            if dt and abs((dt - check_time).total_seconds()) < 300: # 5 min tolerance
                total_price += data['price_rp']
                slots_count += 1
                found = True
                break
        
        if not found:
            # If we run out of data (e.g. tomorrow night), we assume a "neutral" price (e.g. 4 Rp)
            # or the last known price. Let's take a penalty to prefer known data.
            total_price += 5.0 # Penalty/Average
            slots_count += 1

    return total_price / slots_count

def check_optimization_logic(script_conf, state):
    s_id = script_conf['id']
    now = get_current_time()
    
    # 0. PROFILE CHECK
    if check_profile_blocker(script_conf, now):
        return False
    
    # 1. LOAD DATA
    timeline = load_full_timeline(now)
    if not timeline: return True 

    # Find CURRENT status
    current_slot = None
    for ts, data in timeline.items():
        dt = parse_iso_key(ts)
        if dt and 0 <= (dt - now).total_seconds() < 900: 
            current_slot = data; break
    
    if not current_slot: return True 

    # 2. DETERMINE ESTIMATED RUNTIME
    runtime_min = script_conf['initial_runtime_min']
    if s_id in state and state[s_id]['avg_runtime_sec'] > 0:
        runtime_min = int(state[s_id]['avg_runtime_sec'] / 60)
        # Minimum sanity check: 5 min
        if runtime_min < 5: runtime_min = 5
    
    log_debug(f"Optimizing for runtime: {runtime_min} min")

    # 3. CHECK DEADLINE
    last_run = None
    if s_id in state and state[s_id].get("last_run"):
        last_run = datetime.datetime.fromisoformat(state[s_id]["last_run"])
    
    search_deadline = None
    if not last_run:
        log_debug("First run. Scan Global.")
        search_deadline = now + datetime.timedelta(hours=48)
    else:
        mins_since = (now - last_run).total_seconds() / 60
        min_inter = script_conf['min_interval_hours'] * 60
        if mins_since < min_inter:
            log_debug(f"Cooldown active ({int(min_inter - mins_since)}m left).")
            return False
        search_deadline = last_run + datetime.timedelta(hours=script_conf['max_interval_hours'])
        if now >= search_deadline:
            print("   [FORCE] Deadline exceeded!")
            return True

    # 4. FIND BEST WINDOW (Average Cost over Duration)
    future_starts = []
    
    # Calculate current Avg Cost first
    current_avg_cost = get_avg_price_for_duration(now, runtime_min, timeline)
    
    for ts, data in timeline.items():
        dt = parse_iso_key(ts)
        if dt and now <= dt <= search_deadline:
            # Only consider start times that are not blocked
            if not check_profile_blocker(script_conf, dt):
                avg_cost = get_avg_price_for_duration(dt, runtime_min, timeline)
                future_starts.append({'dt': dt, 'avg_cost': avg_cost, 'start_price': data['price_rp'], 'tier': data['tier']})

    if not future_starts: return True
    
    best_option = min(future_starts, key=lambda x: x['avg_cost'])
    
    log_debug(f"Compare: Now (Avg {current_avg_cost:.2f} Rp) vs Best Future ({best_option['dt'].strftime('%H:%M')} Avg {best_option['avg_cost']:.2f} Rp)")
    
    # Decision: Run if current avg cost is close enough to best possible avg cost
    if current_avg_cost <= (best_option['avg_cost'] + 0.1):
        if current_slot['tier'] > script_conf['max_tier']:
             log_debug(f"Best price, but Start-Tier {current_slot['tier']} too high.")
             return False
        print(f"   [OPTIMAL] Starting now. Est. Cost: {current_avg_cost:.2f} Rp/slot.")
        return True
    else:
        print(f"   [WAIT] Better window at {best_option['dt'].strftime('%H:%M')} (Avg {best_option['avg_cost']:.2f} Rp).")
        return False

# ==============================================================================
# 4. MAIN LOOP
# ==============================================================================

def main():
    prevent_double_execution()
    current_ts = get_current_time()
    day_type = get_day_type(current_ts)
    print(f"\n--- EXECUTOR v9.0: {current_ts.strftime('%Y-%m-%d %H:%M:%S')} ({day_type}) ---")
    if DRY_RUN: print("[DEBUG] DRY RUN ACTIVE")

    if is_disk_full():
        print(f"   [ALERT] DISK CRITICAL! Running Emergency Command.")
        if not DRY_RUN: subprocess.run(EMERGENCY_COMMAND, shell=True)
        else: print(f"   [DRY-RUN] Executed: {EMERGENCY_COMMAND}")
        return 

    state = load_state()
    running_processes = []
    active_groups = []
    sorted_scripts = sorted(SCRIPTS_CONFIG, key=lambda x: x.get('order', 99))

    for job in sorted_scripts:
        print(f"\n> Checking {job['id']}...")
        group = job.get('group')
        if group and group in active_groups:
            print(f"   [BLOCK] Group '{group}' is busy.")
            continue

        if check_optimization_logic(job, state):
            if group: active_groups.append(group)
            if not DRY_RUN:
                print(f"   >>> LAUNCHING {job['id']}...")
                try:
                    proc = subprocess.Popen(job['command'], shell=True)
                    running_processes.append({'id': job['id'], 'proc': proc, 'start': time.time()})
                except Exception as e: print(f"   [ERROR] Launch failed: {e}")
            else:
                print(f"   [DRY-RUN] {job['id']} launched.")
                update_runtime_stats(job['id'], job['initial_runtime_min'] * 60)

    if running_processes:
        print(f"\n[PARALLEL] Monitoring {len(running_processes)} jobs...")
        while running_processes:
            for p in running_processes[:]:
                if p['proc'].poll() is not None:
                    dur = int(time.time() - p['start'])
                    print(f"   [DONE] {p['id']} finished in {dur}s.")
                    update_runtime_stats(p['id'], dur)
                    running_processes.remove(p)
            time.sleep(1)
    print("\n--- DONE ---")

if __name__ == "__main__": main()