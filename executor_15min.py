#!/usr/bin/python3
"""
==============================================================================
 MASTER POWER SCRIPT - THE EXECUTOR (v5.1 - DYNAMIC WEEKEND PROFILES)
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
EMERGENCY_COMMAND = "/usr/local/sbin/mover" 

# --- DYNAMIC TIME PROFILES ---
# Hier definierst du verbotene Stunden [0-23].
# Jetzt neu: Unterscheidung zwischen "STANDARD" (Mo-Fr) und "WEEKEND" (Sa/So/Feiertag).
TIME_PROFILES = {
    "STRICT": {
        "STANDARD": [18, 19, 20, 21, 22],      # Werktags: TV-Zeit gesperrt
        "WEEKEND":  [12, 13, 18, 19, 20, 21]   # Wochenende: Mittagessen + Abend gesperrt
    },
    "NIGHT_ONLY": {
        # An allen Tagen gleich: Alles gesperrt ausser 00-06 Uhr
        "STANDARD": [7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23],
        "WEEKEND":  [7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23]
    },
    "IGNORE_TIME": {
        "STANDARD": [],
        "WEEKEND": []
    }
}

# --- SCRIPTS CONFIG ---
SCRIPTS_CONFIG = [
    {
        "id": "Emby_Cache",
        "command": "/usr/bin/python3 /mnt/user/system/scripts/embycache_v2/embycache_run.py",
        "initial_runtime_min": 15,
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
    """Liest aus dem heutigen JSON, ob WEEKEND oder STANDARD ist."""
    fpath = os.path.join(PLANNER_PATH, f"{now.strftime('%Y-%m-%d')}.json")
    if os.path.exists(fpath):
        try:
            data = json.load(open(fpath))
            return data.get('metadata', {}).get('profile_mode', 'STANDARD')
        except: pass
    return 'STANDARD'

def check_profile_blocker(script_conf, now):
    """Prüft Sperrzeiten basierend auf Wochentag."""
    mode_name = script_conf.get("profile_mode", "IGNORE_TIME")
    
    # 1. Welcher Tag ist heute? (STANDARD vs WEEKEND)
    day_type = get_day_type(now)
    
    # 2. Profil laden
    profile_def = TIME_PROFILES.get(mode_name)
    if not profile_def: return False # Unbekanntes Profil -> Erlauben
    
    # 3. Stunden für den Tagestyp laden
    blocked_hours = profile_def.get(day_type, [])
    
    # 4. Prüfen
    if now.hour in blocked_hours:
        log_debug(f"Profile '{mode_name}' blocks hour {now.hour} (Type: {day_type}).")
        return True # BLOCKED
    return False # FREE

# ==============================================================================
# 3. CORE LOGIC
# ==============================================================================

def load_full_timeline(current_time):
    combined = {}
    dates = [current_time.date(), current_time.date() + datetime.timedelta(days=1)]
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

def check_optimization_logic(script_conf, state):
    s_id = script_conf['id']
    now = get_current_time()
    
    # 0. PROFILE CHECK
    if check_profile_blocker(script_conf, now):
        return False
    
    # 1. LOAD DATA
    timeline = load_full_timeline(now)
    if not timeline: return True 

    current_slot = None
    for ts, data in timeline.items():
        dt = parse_iso_key(ts)
        if dt and 0 <= (dt - now).total_seconds() < 900: 
            current_slot = data; break
    
    if not current_slot: return True 

    # 2. CHECK STATE
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

    # 3. FIND BEST PRICE
    future_slots = []
    
    # Pre-calc Day Type for Future Check
    # (Simplified: we assume tomorrow has same profile logic or strict enough)
    # A perfect implementation would check day-type per future slot, 
    # but for simplicity we assume the blocker logic applies to "now" mostly.
    # To be precise, let's just optimize for price within the window.
    
    for ts, data in timeline.items():
        dt = parse_iso_key(ts)
        if dt and now <= dt <= search_deadline:
            # OPTIONAL: Check if future slot is blocked? 
            # For now we assume if it's blocked later, we'll deal with it later.
            future_slots.append({'dt': dt, 'p': data['price_rp'], 't': data['tier']})

    if not future_slots: return True
    best = min(future_slots, key=lambda x: x['p'])
    
    log_debug(f"Compare: Now {current_slot['price_rp']} Rp vs Best {best['p']} Rp")
    
    if current_slot['price_rp'] <= (best['p'] + 0.05):
        if current_slot['tier'] > script_conf['max_tier']:
             log_debug(f"Best price, but Tier {current_slot['tier']} too high.")
             return False
        print("   [OPTIMAL] Starting now.")
        return True
    else:
        print(f"   [WAIT] Better price at {best['dt'].strftime('%H:%M')} ({best['p']} Rp).")
        return False

# ==============================================================================
# 4. MAIN LOOP
# ==============================================================================

def main():
    prevent_double_execution()
    current_ts = get_current_time()
    day_type = get_day_type(current_ts)
    print(f"\n--- EXECUTOR v5.1: {current_ts.strftime('%Y-%m-%d %H:%M:%S')} ({day_type}) ---")
    
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
