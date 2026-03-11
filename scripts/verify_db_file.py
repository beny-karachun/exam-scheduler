"""Verify DB and write results to a UTF-8 file."""
import sqlite3

conn = sqlite3.connect("exam_scheduler_dev.db")
c = conn.cursor()

lines = []

tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
lines.append(f"Tables: {[t[0] for t in tables]}")
for t in tables:
    name = t[0]
    count = c.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    lines.append(f"  {name}: {count} rows")

ext = c.execute("""
    SELECT COUNT(DISTINCT e.student_id)
    FROM enrollments e
    JOIN courses c ON e.course_id = c.id
    WHERE c.ownership_domain = 'EXTERNAL'
""").fetchone()[0]
lines.append(f"\nStudents with >=1 external enrollment: {ext}")

acc = c.execute("SELECT COUNT(*) FROM students WHERE accommodations_multiplier > 1.0").fetchone()[0]
lines.append(f"Accommodated students: {acc}")

lines.append("\nCourses:")
for row in c.execute("SELECT code, name, ownership_domain, duration_minutes, fixed_start_time, fixed_end_time FROM courses"):
    lines.append(f"  {row}")

lines.append("\nEnrollments per course:")
for row in c.execute("""
    SELECT c.code, c.name, COUNT(e.id) as cnt
    FROM courses c JOIN enrollments e ON c.id = e.course_id
    GROUP BY c.id ORDER BY c.code
"""):
    lines.append(f"  {row[0]:<10} {row[1]:<40} {row[2]} students")

conn.close()

with open("scripts/verify_results.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("Results written to scripts/verify_results.txt")
