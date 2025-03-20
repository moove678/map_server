import sqlite3

def get_db_connection():
    conn = sqlite3.connect("users.db")
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        lat REAL DEFAULT 0.0,
        lon REAL DEFAULT 0.0
    )''')
    conn.commit()
    return conn
