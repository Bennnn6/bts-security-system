import subprocess
import os
import sys

# Query processes using PowerShell via subprocess with clean arguments
cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process | Select-Object ProcessId, CommandLine | ConvertTo-Json"]
output = subprocess.check_output(cmd, text=True)

import json
data = json.loads(output)
run_procs = []
for item in data:
    cmdline = item.get("CommandLine") or ""
    pid = item.get("ProcessId")
    if "run.py" in cmdline:
        run_procs.append((pid, cmdline))

print(f"Found {len(run_procs)} processes running run.py:")
for pid, cmdline in run_procs:
    print(f"Terminating PID: {pid} | CMD: {cmdline}")
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=False, capture_output=True)
    except Exception as e:
        print(f"Failed to kill {pid}: {e}")

print("Done terminating stale run.py processes.")

