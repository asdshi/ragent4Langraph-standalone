import sqlite3
conn = sqlite3.connect("checkpoints.sqlite")
c = conn.execute("PRAGMA table_info(writes)")
for row in c.fetchall():
    print(row)
conn.close()
