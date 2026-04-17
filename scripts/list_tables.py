import sqlite3
conn = sqlite3.connect("checkpoints.sqlite")
c = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
for row in c.fetchall():
    print(row[0])
conn.close()
