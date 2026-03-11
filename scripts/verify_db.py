"""Quick verification: query the generated SQLite database and print stats."""
import sqlite3

conn = sqlite3.connect("exam_scheduler_dev.db")
c = conn.cursor()

# List tables
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", [t[0] for t in tables])
for t in tables:
    name = t[0]
    count = c.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    print(f"  {name}: {count} rows")
print()

# Students with external enrollments
ext = c.execute("""
    SELECT COUNT(DISTINCT e.student_id)
    FROM enrollments e
    JOIN courses c ON e.course_id = c.id
    WHERE c.ownership_domain = 'EXTERNAL'
""").fetchone()[0]
print(f"Students with >=1 external enrollment: {ext}")

# Accommodated students
acc = c.execute("SELECT COUNT(*) FROM students WHERE accommodations_multiplier > 1.0").fetchone()[0]
print(f"Accommodated students: {acc}")

# Course breakdown
print("\nCourses:")
for row in c.execute("SELECT code, name, ownership_domain, duration_minutes, fixed_start_time, fixed_end_time FROM courses"):
    print(f"  {row}")

conn.close()
