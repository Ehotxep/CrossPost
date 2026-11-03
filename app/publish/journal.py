"""Persistent, credential-masked publication events shared by the UI and worker."""
from datetime import datetime, timezone
from app import db
from app.jobs import mask_secrets


def record(pub_id, key, state, message):
    if not pub_id:
        return
    with db.tx() as c:
        c.execute('INSERT INTO publication_events(pub_id,at,dest,state,message) VALUES (?,?,?,?,?)',
                  (int(pub_id), datetime.now(timezone.utc).isoformat(timespec='seconds'),
                   key or '', state or '', mask_secrets(str(message or ''))[:2000]))
        c.execute('DELETE FROM publication_events WHERE pub_id=? AND id NOT IN '
                  '(SELECT id FROM publication_events WHERE pub_id=? ORDER BY id DESC LIMIT 500)',
                  (int(pub_id), int(pub_id)))


def read(pub_id):
    rows = db.query('SELECT at,dest,state,message FROM publication_events WHERE pub_id=? ORDER BY id DESC LIMIT 200',
                    (int(pub_id),))
    return list(reversed(rows))
