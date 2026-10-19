import json
import logging
import jieba
from . import db

jieba.setLogLevel(logging.WARNING)


def tokens(text):
    return " ".join(x for x in jieba.cut_for_search(text) if x.strip())


def index_document(doc):
    # Keep only the newest indexed run for an asset. Documents and evidence versions remain untouched.
    with db.connect() as c:
        ids = [x[0] for x in c.execute("SELECT id FROM search_chunks WHERE asset_id=?", (doc["asset_id"],))]
        for i in ids:
            c.execute("DELETE FROM search_fts WHERE rowid=?", (i,))
        c.execute("DELETE FROM search_chunks WHERE asset_id=?", (doc["asset_id"],))
        chunks = [("title", 0, doc["overview"]["title"] + " " + doc["overview"]["summary"])]
        if doc.get("summary_markdown"):
            chunks.append(("summary", 0, doc["summary_markdown"]))
        chunks += [("transcript", t["start_ms"], t["raw_text"]) for t in doc["transcript"]]
        chunks += [
            (
                "step",
                s["start_ms"],
                s["title"] + " " + s["action"] + " " + json.dumps(s["parameters"], ensure_ascii=False),
            )
            for ch in doc["chapters"]
            for s in ch["steps"]
        ]
        chunks += [
            ("screen", o["start_ms"], o["description"] + " " + " ".join(t["text"] or "" for t in o["visible_texts"]))
            for o in doc["observations"]
        ]
        for kind, ms, text in chunks:
            cursor = c.execute(
                "INSERT INTO search_chunks(asset_id,run_id,kind,start_ms,raw_text) VALUES (?,?,?,?,?)",
                (doc["asset_id"], doc["run_id"], kind, ms, text),
            )
            c.execute("INSERT INTO search_fts(rowid,search_text) VALUES (?,?)", (cursor.lastrowid, tokens(text)))


def search(query, limit=30):
    query = query.strip()[:200]
    if not query:
        return []
    parts = list(dict.fromkeys(tokens(query).split()))[:16]
    match = " OR ".join('"' + p.replace('"', '""') + '"' for p in parts)
    found = (
        db.rows(
            """SELECT c.*,a.title FROM search_fts f JOIN search_chunks c ON c.id=f.rowid JOIN assets a ON c.asset_id=a.id
                    WHERE search_fts MATCH ? ORDER BY rank LIMIT ?""",
            (match, limit),
        )
        if match
        else []
    )
    if len(found) < limit:
        literal = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        extra = db.rows(
            "SELECT c.*,a.title FROM search_chunks c JOIN assets a ON c.asset_id=a.id WHERE raw_text LIKE ? ESCAPE '\\' LIMIT ?",
            ("%" + literal + "%", limit),
        )
        seen = {x["id"] for x in found}
        found += [x for x in extra if x["id"] not in seen]
    return found[:limit]
