"""
Iterates zones and tries at least 2 GPU types (T4 + L4)

Uses 3 methods to check the availability:

Method A: "offered in zone" via accelerator-types list (1 call total)
Method B: quota check (project-info + all the regions described)
Method C: attempt a real allocation with --no-address to create and delete

Categorizes failures (e.g., “No GPUs available,” “Quota exceeded,” “Pricing too high”)

Writes a table to gpu_results.csv with >=10 tested zones and their details about GPU, 
reason for failure, and times for each check

To Run the script, please use the command below:
  python3 gpu_assign.py
"""

import subprocess
import json
import time
import csv
import uuid
from collections import defaultdict

# specify the configurations to use 
GPU_TYPES = ["nvidia-tesla-t4", "nvidia-l4"]     # minimum two GPU types being used here
TEST_ZONES = 15                                  # test with 15 zones
DO_REAL_CREATE = True                            # True is if create n delete worked otherwise dry-run only

# T4 is compatible with n1-standard-4 and L4 often wants g2-standard-4
MACHINE_TYPE_BY_GPU = {
    "nvidia-tesla-t4": "n1-standard-4",
    "nvidia-l4": "g2-standard-4",
}

IMAGE_FAMILY = "debian-11"
IMAGE_PROJECT = "debian-cloud"

# use the helper commands given below
def run_cmd(cmd):
    t0 = time.time()
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()
        return True, out, time.time() - t0
    except subprocess.CalledProcessError as e:
        return False, e.output.decode(), time.time() - t0

def get_project_id():
    ok, out, _ = run_cmd(["gcloud", "config", "get-value", "project", "--quiet"])
    if not ok:
        return None
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return lines[-1] if lines else None

def region_from_zone(zone: str) -> str:
    parts = zone.rsplit("-", 1)
    if len(parts) == 2:
        return parts[0]
    # if expected format wrong return zone
    return zone

def quota_has_capacity(quotas: dict, metric_candidates: list) -> bool:
    # minimum 1 free unit -> return true
    if not quotas or not metric_candidates:
        return False

    idx = 0
    while idx < len(metric_candidates):
        m = metric_candidates[idx]
        if m in quotas:
            limit, usage = quotas[m]
            free = limit - usage
            if free >= 1:
                return True
            elif free == 0:
                pass 
            else:
                pass
        idx += 1

    return False

def categorize_error(err_text: str) -> str:
    u = err_text.upper()

    # external IP can get blocked by org so make that check here
    if "CONSTRAINT" in u and ("VMEXTERNALIPACCESS" in u or "EXTERNALIP" in u):
        return "External IP blocked by policy"
    elif "ZONE_RESOURCE_POOL_EXHAUSTED" in u or "RESOURCE_POOL_EXHAUSTED" in u:
        return "No GPUs available (zone exhausted)"
    elif "QUOTA" in u or "QUOTA_EXCEEDED" in u or "GPUS_ALL_REGIONS" in u:
        return "Quota exceeded"
    elif ("NOT FOUND" in u and "ACCELERATOR" in u) or ("INVALID" in u and "ACCELERATOR" in u):
        return "GPU type not offered in zone"
    elif "PERMISSION_DENIED" in u or "NOT_AUTHORIZED" in u:
        return "Permission denied"
    elif "BILLING" in u or "CREDIT" in u or "PAYMENT" in u:
        return "Pricing/Billing issue"
    elif "TIMEOUT" in u or "DEADLINE" in u:
        return "Request timed out"
    else:
        return "Other error"

# method A here where we check for GPU offerings 
def get_gpu_offering_map():
    # do only 1 api call and return dict gpu_type which will set where zones are offered
    ok, out, _ = run_cmd(["gcloud", "compute", "accelerator-types", "list", "--format=json"])
    offering = defaultdict(set)

    if not ok:
        print("Warning: could not fetch accelerator types. GPU offering map will be empty.")
        return offering

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        print("Warning: failed to parse accelerator-types JSON.")
        return offering

    i = 0
    while i < len(data):
        item = data[i]
        name = item.get("name")
        zone_raw = item.get("zone", "")
        zone = zone_raw.split("/")[-1] if "/" in zone_raw else zone_raw
        if name and zone:
            offering[name].add(zone)
        i += 1

    return offering

# method B: we check for the quotas and assign accordingly
def get_global_quotas():
    ok, out, _ = run_cmd(["gcloud", "compute", "project-info", "describe", "--format=json"])
    quotas = {}
    if not ok:
        return quotas

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return quotas

    for q in data.get("quotas", []):
        metric = q.get("metric")
        if metric:
            quotas[metric] = (q.get("limit", 0.0), q.get("usage", 0.0))

    return quotas

def get_region_quotas(region: str):
    # do only 1 API call per region
    ok, out, _ = run_cmd(["gcloud", "compute", "regions", "describe", region, "--format=json"])
    quotas = {}

    if not ok:
        return quotas

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return quotas

    quota_list = data.get("quotas", [])
    idx = 0
    while idx < len(quota_list):
        q = quota_list[idx]
        metric = q.get("metric")
        if metric:
            quotas[metric] = (q.get("limit", 0.0), q.get("usage", 0.0))
        idx += 1

    return quotas

# method C: go based on allocation 
def attempt_create_and_delete(zone: str, gpu_type: str):
    # if we are able to create and delete VM then we have tested the availability
    # add the --no-address for getting around the org policy and not run into issues there
    instance_name = f"gpu-probe-{uuid.uuid4().hex[:8]}"
    machine_type = MACHINE_TYPE_BY_GPU.get(gpu_type)

    if machine_type is None:
        machine_type = "n1-standard-4"
        print(f"Warning: no machine type mapped for {gpu_type}, falling back to {machine_type}")

    create_cmd = [
        "gcloud", "compute", "instances", "create", instance_name,
        "--zone", zone,
        "--machine-type", machine_type,
        "--accelerator", f"type={gpu_type},count=1",
        "--maintenance-policy", "TERMINATE",
        "--restart-on-failure",
        "--image-family", IMAGE_FAMILY,
        "--image-project", IMAGE_PROJECT,
        "--no-address",
        "--quiet",
    ]

    ok, out, elapsed = run_cmd(create_cmd)

    if ok:
        print(f"VM '{instance_name}' was created successfully in {zone} with {gpu_type}. will delete it now!")
        del_ok, del_out, _ = run_cmd([
            "gcloud", "compute", "instances", "delete", instance_name,
            "--zone", zone, "--quiet"
        ])
        if not del_ok:
            print(f"Warning: created {instance_name} but failed to delete it: {del_out[:200]}")
        return True, "Allocated successfully", elapsed
    else:
        reason = categorize_error(out)
        print(f"VM creation failed in {zone} with {gpu_type}. reason was: {reason}")
        return False, reason, elapsed

def attempt_dry_run(zone: str, gpu_type: str):
    # try the dry run but do not allocate which will be good for 0 cost runs
    machine_type = MACHINE_TYPE_BY_GPU.get(gpu_type, "n1-standard-4")
    cmd = [
        "gcloud", "--dry-run", "compute", "instances", "create", "dryrun-temp",
        "--zone", zone,
        "--machine-type", machine_type,
        "--accelerator", f"type={gpu_type},count=1",
        "--maintenance-policy", "TERMINATE",
        "--restart-on-failure",
        "--image-family", IMAGE_FAMILY,
        "--image-project", IMAGE_PROJECT,
        "--no-address",
        "--quiet",
    ]
    ok, out, elapsed = run_cmd(cmd)
    if ok:
        return False, "Dry-run only with no allocation", elapsed
    else:
        return False, categorize_error(out), elapsed


def main():
    project = get_project_id()
    if not project:
        print("ERROR: Could not detect project. Run: gcloud config set project <id>")
        return

    print(f"Project: {project}")
    print("Fetching GPU offerings (1 call)..")
    offering = get_gpu_offering_map()

    print("Fetching global quotas (1 call)..")
    global_quotas = get_global_quotas()
    global_has_gpu = quota_has_capacity(global_quotas, ["GPUS_ALL_REGIONS"])
    print(f"Check for Global GPU quota capacity (GPUS_ALL_REGIONS): {'YES' if global_has_gpu else 'NO'}")

    ok, out, _ = run_cmd(["gcloud", "compute", "zones", "list", "--format=value(name)"])
    if not ok:
        print("ERROR: Failed to list zones:", out)
        return
    all_zones = [line.strip() for line in out.splitlines() if line.strip()]

    if not all_zones:
        print("ERROR: No zones found. Something is wrong with the gcloud setup.")
        return

    # quick filter to prioritize zones that offer GPU types
    candidates = [z for z in all_zones if any(z in offering.get(g, set()) for g in GPU_TYPES)]
    if not candidates:
        print("Note: offering map is empty or no zones matched — falling back to all zones.")
        candidates = all_zones[:]

    region_quota_cache = {}

    results = []
    tested = 0
    candidate_idx = 0

    while tested < TEST_ZONES and candidate_idx < len(candidates):
        zone = candidates[candidate_idx]
        candidate_idx += 1

        region = region_from_zone(zone)

        # do method b to do regional quota checks with times
        t0 = time.time()
        if region not in region_quota_cache:
            region_quota_cache[region] = get_region_quotas(region)
        region_quotas = region_quota_cache[region]
        quota_elapsed = time.time() - t0

        # quota-based availability for target GPU types
        quota_metrics = ["NVIDIA_T4_GPUS", "NVIDIA_L4_GPUS"]
        quota_based_available = global_has_gpu and quota_has_capacity(region_quotas, quota_metrics)

        # method c: attempt allocation for each GPU type
        allocated = False
        final_reason = "Not attempted"
        alloc_elapsed_total = 0.0
        gpu_used = ""

        gpu_idx = 0
        while gpu_idx < len(GPU_TYPES) and not allocated:
            gpu = GPU_TYPES[gpu_idx]

            # method a: offer check but do no api call
            offered_in_zone = zone in offering.get(gpu, set())

            if not offered_in_zone:
                final_reason = "GPU type not offered in zone"
                gpu_idx += 1
                continue

            if DO_REAL_CREATE:
                ok_alloc, reason, t_alloc = attempt_create_and_delete(zone, gpu)
            else:
                ok_alloc, reason, t_alloc = attempt_dry_run(zone, gpu)

            alloc_elapsed_total += t_alloc

            if ok_alloc:
                allocated = True
                final_reason = "None"
                gpu_used = gpu
            else:
                final_reason = reason

            gpu_idx += 1

        results.append({
            "zone": zone,
            "region": region,
            "methodA_offered_any": "Yes" if any(zone in offering.get(g, set()) for g in GPU_TYPES) else "No",
            "methodB_quota_says_available": "Yes" if quota_based_available else "No",
            "gpu_allocated_successfully": "Yes" if allocated else "No",
            "gpu_used": gpu_used,
            "failure_reason": final_reason if not allocated else "None",
            "quota_check_time_s": round(quota_elapsed, 3),
            "allocation_attempt_time_s": round(alloc_elapsed_total, 3),
        })

        tested += 1

    if tested < TEST_ZONES:
        print(f"Warning: only found {tested} candidate zones (needed {TEST_ZONES}).")

    print("\nRUN RESULTS")
    for r in results:
        print(r)

    # Write CSV
    if results:
        csv_name = "gpu_results.csv"
        with open(csv_name, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
        print(f"\nWrote table to: {csv_name}")
    else:
        print("No results to write.")

    print("\nNotes:")
    print("methodB_quota_says_available=Yes means quota allows GPUs, but real allocation can fail due to capacity.")
    print("If failures show 'External IP blocked by policy', ensure --no-address is present (it is specified in the script).")

if __name__ == "__main__":
    main()
