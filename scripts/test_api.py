"""Test the FastAPI endpoints against the running server."""
import subprocess, sys, json

BASE = "http://127.0.0.1:8000"

def run(label, method, path, data=None):
    url = f"{BASE}{path}"
    cmd = [sys.executable, "-c", f"""
import urllib.request, json
req = urllib.request.Request("{url}", method="{method}")
""" + (f"""
req.add_header("Content-Type", "application/json")
req.data = json.dumps({json.dumps(data)}).encode()
""" if data else "") + f"""
try:
    resp = urllib.request.urlopen(req)
    body = resp.read().decode()
    print(f"{{resp.status}} {{body[:500]}}")
except urllib.error.HTTPError as e:
    print(f"{{e.code}} {{e.read().decode()[:500]}}")
except Exception as e:
    print(f"ERROR {{e}}")
"""]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = result.stdout.strip() or result.stderr.strip()
    return f"  [{label}] {out}"

lines = []
lines.append("=== API Endpoint Tests ===\n")

# 1. List courses
lines.append(run("GET /api/courses", "GET", "/api/courses"))

# 2. List rooms
lines.append(run("GET /api/rooms", "GET", "/api/rooms"))

# 3. Validate-move (should pass - BIO330 to Monday 14:00, room 2)
lines.append(run(
    "POST /validate-move (should PASS)",
    "POST", "/api/schedule/validate-move",
    {"course_id": 1, "new_start_time": "2026-03-19T14:00:00Z", "new_room_id": 2}
))

# 4. Validate-move (should FAIL - during MATH201 blackout, Mon 09:00-12:00 + buffer)
lines.append(run(
    "POST /validate-move (should FAIL - blackout)",
    "POST", "/api/schedule/validate-move",
    {"course_id": 1, "new_start_time": "2026-03-16T10:00:00Z", "new_room_id": 1}
))

# 5. Validate-move (room too small)
lines.append(run(
    "POST /validate-move (should FAIL - capacity)",
    "POST", "/api/schedule/validate-move",
    {"course_id": 1, "new_start_time": "2026-03-19T14:00:00Z", "new_room_id": 4}
))

# 6. Trigger solver
lines.append(run(
    "POST /solve",
    "POST", "/api/schedule/solve",
    {"exam_period_start": "2026-03-16T08:00:00Z", "exam_period_end": "2026-03-21T20:00:00Z"}
))

# 7. NLP constraint (should return 503 - no API key)
lines.append(run(
    "POST /nlp-constraint (expect 503)",
    "POST", "/api/ai/nlp-constraint",
    {"text": "Schedule Genetics on Tuesday morning"}
))

# 8. Health check via docs
lines.append(run("GET /docs", "GET", "/docs"))

with open("scripts/api_test_output.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("Results saved to scripts/api_test_output.txt")
