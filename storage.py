"""Durable deduplication. Never use Render's ephemeral disk for this ledger."""
import psycopg

DDL = '''CREATE TABLE IF NOT EXISTS telegram_updates (
    update_id BIGINT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('sending', 'sent', 'uncertain')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)'''


class Ledger:
    def __init__(self, url):
        self.url = url

    def connect(self):
        return psycopg.connect(self.url, connect_timeout=10, options='-c statement_timeout=10000')

    def initialize(self):
        with self.connect() as conn:
            conn.execute(DDL)

    def healthy(self):
        with self.connect() as conn:
            conn.execute('SELECT 1 FROM telegram_updates LIMIT 1')

    def contains(self, update_id):
        with self.connect() as conn:
            return conn.execute('SELECT 1 FROM telegram_updates WHERE update_id=%s', (update_id,)).fetchone() is not None

    def claim(self, update_id):
        # Commit BEFORE Telegram: concurrent requests and restarts cannot send twice.
        with self.connect() as conn:
            row = conn.execute("INSERT INTO telegram_updates(update_id,state) VALUES (%s,'sending') ON CONFLICT DO NOTHING RETURNING update_id", (update_id,)).fetchone()
            return row is not None

    def finish(self, update_id, state):
        with self.connect() as conn:
            conn.execute('UPDATE telegram_updates SET state=%s WHERE update_id=%s', (state, update_id))

    def release(self, update_id):
        # Only use after an explicit Telegram rejection that guarantees no send.
        with self.connect() as conn:
            conn.execute('DELETE FROM telegram_updates WHERE update_id=%s', (update_id,))
