"""Capture solver test output to file."""
import subprocess, sys

result = subprocess.run(
    [sys.executable, "-m", "scripts.test_solver"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
)
with open("scripts/solver_output.txt", "w", encoding="utf-8") as f:
    f.write("=== STDOUT ===\n")
    f.write(result.stdout or "(empty)")
    f.write("\n=== STDERR ===\n")
    f.write(result.stderr or "(empty)")
    f.write(f"\n=== EXIT CODE: {result.returncode} ===\n")
print(f"Output saved. Exit code: {result.returncode}")
