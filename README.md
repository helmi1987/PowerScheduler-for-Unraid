# ⚡ Power Scheduler for Unraid

Price-driven task scheduling for Unraid, based on the dynamic grid tariff of CKW (Lucerne, CH).

| Script | Version | Schedule | Purpose |
| --- | --- | --- | --- |
| `power_planner.py` | v6.3 | Daily (e.g. `0 13 * * *`) | Fetches CKW prices, computes tiers, writes one JSON plan per day |
| `executor_15min.py` | v9.0 (Duration Aware) | `*/15 * * * *` | Decides per job whether to start now, based on the plans |

Dependencies: Python 3 standard library only.

## Contents

1. [Features](#features)
2. [Installation](#installation)
3. [Planner](#planner)
4. [Executor](#executor)
5. [Configuration](#configuration)
6. [Tier Logic](#tier-logic)
7. [Examples](#examples)
8. [Emergency System (Disk Full)](#emergency-system-disk-full)
9. [Files & Paths](#files--paths)
10. [FAQ & Troubleshooting](#faq--troubleshooting)
11. [Known Limitations](#known-limitations)

## Features

- **Dynamic prices** – CKW API (`tariff_name=home_dynamic`, `tariff_type=grid_usage`), CHF converted to Rappen.
- **Hard cap** – slots above `HARD_CAP_RP` (6.0 Rp) are marked `BLOCKED` / tier 99.
- **Tiers 1–20** – relative price rank per day.
- **Duration aware** – compares the *average* price over the expected runtime of a job, not only the start slot.
- **Self-learning runtime** – moving average of the last 30 runs per job.
- **Cooldown & deadline** – `min_interval_hours` / `max_interval_hours` per job.
- **Time profiles** – blocked hours, separate for workdays and weekends/holidays.
- **Groups & order** – only one job per group is started per executor run.
- **Parallel execution** – jobs of different groups start in the same run.
- **Disk full protection** – runs an emergency command when the cache exceeds a threshold.
- **Single instance** – file lock prevents overlapping executor runs.
- **Dry run** – logs decisions without executing commands, uses a separate state file.

## Installation

1. Install the **User Scripts** plugin (Unraid → Apps).
2. Create script `Power_Planner_Daily`, paste `power_planner.py`, schedule **Custom** `0 13 * * *` (or **Daily**).
3. Create script `Power_Executor_15min`, paste `executor_15min.py`, schedule **Custom** `*/15 * * * *`.
4. Edit `SCRIPTS_CONFIG` in the executor. `DRY_RUN = True` is the default in the code.
5. Run the **planner** first, then the **executor** manually ("Run Script") and check the logs.
6. When the output is as expected, set `DRY_RUN = False`.

## Planner

`power_planner.py` – flow per run:

1. Creates `STORAGE_PATH` (`/mnt/user/appdata/power_scheduler/`) if missing.
2. Deletes every `YYYY-MM-DD.json` with a date before today (other files are left untouched).
3. Targets: always **tomorrow**; **today** additionally if today's file is missing (self-healing).
4. Fetches 00:00:00–23:59:59 of the target date from the CKW API. The UTC offset (`+01:00` / `+02:00`) is taken from the *current* local DST state.
5. Builds the timeline (price in Rp, tier, status) and calendar metadata. A slot without a price value in the API response is stored as 0.0 Rp (and therefore lands in tier 1).
6. Writes `<STORAGE_PATH>/YYYY-MM-DD.json`.

Calendar (`profile_mode` in the plan):

- `WEEKEND` – Saturday, Sunday and fixed holidays: 1.1., 2.1., 1.5., 1.8., 15.8., 1.11., 8.12., 25.12., 26.12.
- `STANDARD` – all other days.
- Movable holidays (Easter, Ascension, Whitsun, Corpus Christi) are **not** handled.

Plan format:

```json
{
    "metadata": {
        "target_date": "2026-10-10",
        "generated_at": "2026-10-09T13:00:02.123456",
        "profile_mode": "WEEKEND",
        "calendar_reason": "WEEKEND",
        "hard_cap_rp": 6.0
    },
    "timeline": {
        "<start_timestamp from API>": { "price_rp": 4.1234, "tier": 3, "status": "ALLOWED" }
    }
}
```

## Executor

`executor_15min.py` – flow per run:

1. Acquires lock `/tmp/power_executor.lock`. If already locked → exits silently (`exit 0`, no log output).
2. Disk check (see [Emergency System](#emergency-system-disk-full)). If triggered → no jobs this run.
3. Sorts `SCRIPTS_CONFIG` by `order` (default 99).
4. Per job:
   - Group already started in this run → `[BLOCK] Group '<group>' is busy.`
   - Otherwise the decision logic below; on **true** the command is started (`subprocess.Popen`, `shell=True`).
5. Waits until all started processes have finished, measures the runtime and updates the state file. The lock is held during this time.

Decision logic (`check_optimization_logic`):

| Step | Condition | Result |
| --- | --- | --- |
| 0 | Current hour is in the blocked hours of the job's time profile (day type from today's plan, fallback `STANDARD`) | wait |
| 1 | No plan data (today, +1, +2 days) available | **start** |
| 1 | No timeline slot starting within the next 15 min (`0 ≤ slot − now < 15 min`) | **start** |
| 2 | Runtime estimate: `avg_runtime_sec / 60` from state (min. 5 min), otherwise `initial_runtime_min` | – |
| 3 | No `last_run` (first run) | search window = now + 48 h |
| 3 | Time since last run < `min_interval_hours` | wait (cooldown) |
| 3 | Now ≥ `last_run + max_interval_hours` | **start** (`[FORCE]`, ignores price and tier) |
| 4 | Average price over runtime for "now" and for every future slot start up to the deadline (slots blocked by the profile are skipped) | – |
| 4 | No candidate | **start** |
| 5 | Avg(now) ≤ best Avg + 0.1 Rp **and** tier of that next slot ≤ `max_tier` | **start** (`[OPTIMAL]`) |
| 5 | Avg(now) ≤ best Avg + 0.1 Rp, but tier of that next slot > `max_tier` | wait |
| 5 | Otherwise | wait (`[WAIT] Better window at HH:MM`) |

Average price calculation: one 15-min slot per started 15 min of runtime. Slot match tolerance ±5 min. Missing slots (no plan data) count as **5.0 Rp**.

## Configuration

### Global (executor)

| Variable | Default | Description |
| --- | --- | --- |
| `OVERRIDE_NOW` | `""` | Simulated time, format `YYYY-MM-DD HH:MM`. Empty = real time. |
| `DRY_RUN` | `True` | No commands are executed; uses `executor_state_dryrun.json`. |
| `PLANNER_PATH` | `/mnt/user/appdata/power_scheduler/` | Must match `STORAGE_PATH` of the planner. |
| `LOCK_FILE_PATH` | `/tmp/power_executor.lock` | Lock file. |
| `DISK_PATH_CHECK` | `/mnt/cache` | Monitored mount point. |
| `DISK_FULL_THRESHOLD` | `90` | Usage in % that triggers the emergency command. |
| `EMERGENCY_COMMAND` | `""` | Command run on disk full (e.g. `/usr/local/sbin/mover`). |
| `TIME_PROFILES` | see below | Blocked hours per profile and day type. |

### Global (planner)

| Variable | Default | Description |
| --- | --- | --- |
| `API_URL` | CKW endpoint | Dynamic price API. |
| `HARD_CAP_RP` | `6.0` | Price in Rp above which a slot is `BLOCKED` (tier 99). |
| `STORAGE_PATH` | `/mnt/user/appdata/power_scheduler/` | Output directory for plans. |
| `FILENAME_FORMAT` | `%Y-%m-%d.json` | Plan file name. |

### Time profiles

Listed hours are **blocked** (start not allowed).

| Profile | STANDARD | WEEKEND |
| --- | --- | --- |
| `STRICT` | 18–22 | 12, 13, 18–22 |
| `IGNORE_TIME` | – | – |
| `NIGHT_ONLY` | 7–23 | 7–23 |

An unknown profile name is treated as "no restriction".

### Job parameters (`SCRIPTS_CONFIG`)

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | String | yes | Unique name; key in the state file. |
| `command` | String | yes | Shell command (`shell=True`). |
| `initial_runtime_min` | Int | yes | Runtime estimate until a measured average exists. Also used as runtime in dry run. |
| `min_interval_hours` | Int | yes | Cooldown between two starts. |
| `max_interval_hours` | Int | yes | Deadline after the last start; reached → forced start. |
| `max_tier` | Int | yes | Highest allowed tier (1–20) of the start slot. |
| `profile_mode` | String | no | Time profile, default `IGNORE_TIME`. |
| `group` | String | no | Only one job per group is started per executor run. |
| `order` | Int | no | Processing order (ascending), default 99. |

Missing required parameters cause a `KeyError`.

## Tier Logic

1. All slots of the day with price ≤ `HARD_CAP_RP` are collected and sorted ascending.
2. `rank` = index of the first occurrence of the price in that list.
3. `percentile = rank / count * 100`
4. `tier = floor(percentile / 5) + 1`, max. 20.
5. Slots above the hard cap: `tier = 99`, `status = "BLOCKED"`.

| Tier | Percentile | Meaning |
| --- | --- | --- |
| 1 | 0 – <5 % | Cheapest 5 % of the day |
| 5 | 20 – <25 % | Lower quarter |
| 11 | 50 – <55 % | Median range |
| 20 | 95 – 100 % | Most expensive slots below the hard cap |
| 99 | – | Above hard cap |

Tiers are relative per day. A tier-99 slot is only started via forced start (deadline) or when no plan data is available.

## Examples

### Unraid Mover – daily, not during TV time

```python
{
    "id": "Mover",
    "command": "/usr/local/sbin/mover",
    "initial_runtime_min": 30,
    "min_interval_hours": 20,
    "max_interval_hours": 48,
    "max_tier": 10,
    "profile_mode": "STRICT"
}
```

### Backup chain – group with order

```python
{
    "id": "Local_Backup",
    "command": "bash /mnt/user/scripts/backup.sh >> /mnt/user/logs/backup.log 2>&1",
    "initial_runtime_min": 45,
    "min_interval_hours": 24,
    "max_interval_hours": 30,
    "max_tier": 10,
    "profile_mode": "STRICT",
    "group": "backup_chain",
    "order": 1
},
{
    "id": "Cloud_Upload",
    "command": "bash /mnt/user/scripts/upload.sh >> /mnt/user/logs/upload.log 2>&1",
    "initial_runtime_min": 60,
    "min_interval_hours": 24,
    "max_interval_hours": 30,
    "max_tier": 10,
    "profile_mode": "STRICT",
    "group": "backup_chain",
    "order": 2
}
```

Behaviour: if `Local_Backup` starts, `Cloud_Upload` is skipped in the same run. Because the executor holds the lock until all started jobs have finished, `Cloud_Upload` can start at the earliest in a run after `Local_Backup` has finished. There is **no** check whether `Local_Backup` ran successfully or ran at all.

### Emby cache – background job, no time restriction (default in code)

```python
{
    "id": "Emby_Cache",
    "command": "/usr/bin/python3 /mnt/user/system/scripts/embycache_v2/embycache_run.py",
    "initial_runtime_min": 30,
    "min_interval_hours": 20,
    "max_interval_hours": 28,
    "max_tier": 5,
    "profile_mode": "IGNORE_TIME",
    "group": "media",
    "order": 1
}
```

## Emergency System (Disk Full)

Checked at the beginning of every executor run:

- **Trigger:** usage of `DISK_PATH_CHECK` > `DISK_FULL_THRESHOLD` % (based on `f_bavail / f_blocks`).
- **Action:** `EMERGENCY_COMMAND` is executed (synchronously); in dry run only logged.
- **Effect:** all jobs are skipped in this run. Repeats every run as long as the threshold is exceeded.
- Errors of `statvfs` → treated as "not full".

```python
DISK_PATH_CHECK = "/mnt/cache"
DISK_FULL_THRESHOLD = 90
EMERGENCY_COMMAND = "/usr/local/sbin/mover"
```

With the default `EMERGENCY_COMMAND = ""` nothing is executed, but jobs are still skipped.

## Files & Paths

| Path | Written by | Content |
| --- | --- | --- |
| `/mnt/user/appdata/power_scheduler/YYYY-MM-DD.json` | Planner | Daily plan |
| `/mnt/user/appdata/power_scheduler/executor_state.json` | Executor | Runtime history (max. 30), average, last run |
| `/mnt/user/appdata/power_scheduler/executor_state_dryrun.json` | Executor (dry run) | Same as above, dry run only |
| `/tmp/power_executor.lock` | Executor | Lock file |

State format:

```json
{
    "Emby_Cache": {
        "history": [1800, 1650],
        "avg_runtime_sec": 1725.0,
        "last_run": "2026-10-09T02:15:00.123456"
    }
}
```

Note: `last_run` is written when the job **finishes**, not when it starts.

## FAQ & Troubleshooting

**Reset the learned state (simulate a first run)**

```bash
rm /mnt/user/appdata/power_scheduler/executor_state.json
# dry run:
rm /mnt/user/appdata/power_scheduler/executor_state_dryrun.json
```

**Test safely**

`DRY_RUN = True`: no commands are executed, `[DEBUG]` lines show the decision path. In dry run, a "started" job is immediately recorded with `initial_runtime_min` as runtime, so cooldowns apply in subsequent dry runs as well. Use `OVERRIDE_NOW` to simulate other times.

**Executor produces no output**

Another executor instance holds the lock (jobs still running). The script exits without a message.

**Where are the job logs?**

The executor does not capture job output. Use redirection in `command` (e.g. `>> /mnt/user/logs/job.log 2>&1`). Executor output itself is shown in the User Scripts log.

**Job starts despite high price**

Possible causes: deadline exceeded (`[FORCE]`), no plan files available, or no timeline slot for the current 15 min.

## Known Limitations

- Movable holidays (Easter etc.) are not detected.
- Planner uses the DST offset of the run date for the target date; on DST switch days the request window can be off by one hour.
- Timestamp offsets in the plan are ignored by the executor (parsed as local time).
- The tier checked against `max_tier` is the one of the slot that starts within the next 15 min, not of the slot already running.
- Slots without a price in the API response are stored as 0.0 Rp / tier 1.
- While any started job is running, the lock blocks all further executor runs – including jobs of other groups and the disk check.
- A forced start (deadline) ignores hard cap and `max_tier`, but not the time profile.
