import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

class SQLiteDBWrapper:
    """SQLite için psycopg2 uyumlu DBWrapper (conn.execute ve row['col'] desteği)."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=None):
        cur = self.conn.cursor()
        # %s parametrelerini SQLite için ? karakterine çevir
        sqlite_query = query.replace('%s', '?')
        if params:
            cur.execute(sqlite_query, params)
        else:
            cur.execute(sqlite_query)
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


class PostgresDBWrapper:
    """PostgreSQL için psycopg2 DBWrapper."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=None):
        from psycopg2.extras import DictCursor
        cur = self.conn.cursor(cursor_factory=DictCursor)
        if params:
            cur.execute(query, params)
        else:
            cur.execute(query)
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def get_db_connection():
    db_url = os.getenv('DATABASE_URL')
    
    # Render veya canlı ortamda PostgreSQL varsa
    if db_url and db_url.startswith(('postgres://', 'postgresql://')):
        import psycopg2
        # Render bazen postgres:// verir, psycopg2 postgresql:// ister
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        conn = psycopg2.connect(db_url)
        return PostgresDBWrapper(conn)
    else:
        # Yerel geliştirme ortamında SQLite kullan
        db_path = os.path.join(os.path.dirname(__file__), 'ozelders.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return SQLiteDBWrapper(conn)


def init_db():
    try:
        db_url = os.getenv('DATABASE_URL')
        is_postgres = bool(db_url and db_url.startswith(('postgres://', 'postgresql://')))
        
        conn = get_db_connection()
        if is_postgres:
            schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
            if os.path.exists(schema_path):
                with open(schema_path, 'r', encoding='utf-8') as f:
                    schema = f.read()
                    conn.execute(schema)
                    conn.commit()
        else:
            # SQLite için şema
            sqlite_schema = """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                grade_level TEXT NOT NULL,
                parent_contact TEXT,
                hourly_rate REAL NOT NULL,
                default_duration INTEGER DEFAULT 60,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id),
                lesson_date TEXT NOT NULL,
                duration_minutes INTEGER DEFAULT 60,
                status TEXT DEFAULT 'planlandi',
                topic TEXT,
                homework TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lesson_id INTEGER NOT NULL REFERENCES lessons(id),
                amount REAL NOT NULL,
                is_paid INTEGER DEFAULT 0,
                payment_date TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT,
                grade_level TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS quick_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
            cur = conn.conn.cursor()
            cur.executescript(sqlite_schema)
            conn.commit()
        
        conn.close()
    except Exception as e:
        print(f"init_db uyarısı: {e}")

if __name__ == '__main__':
    init_db()
    print("Veritabanı hazırlandı.")
