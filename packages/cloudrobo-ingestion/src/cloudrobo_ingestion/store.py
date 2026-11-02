"""Single-writer, batch-only durable recovery state."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid

logger = logging.getLogger(__name__)
DEFERRED_REPORT_TTL_MS = 24 * 60 * 60 * 1000
RESULT_SEND_COUNT = 3


def result_send_limit(kind):
    return 1 if kind == "failure" else RESULT_SEND_COUNT


def now_ms():
    return time.time_ns() // 1_000_000


class BatchStore:
    def __init__(self, config):
        from .workspace import bind_mode

        bind_mode(config)
        self.db = sqlite3.connect(config.work_dir / "batches.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.on_close = None
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, asset_path TEXT NOT NULL,
                timestamp INTEGER NOT NULL, task_type TEXT NOT NULL, collect_method TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', error_info TEXT,
                cleanup_error_info TEXT,
                cleanup_pending INTEGER NOT NULL DEFAULT 0, pending BLOB,
                report_enabled INTEGER NOT NULL DEFAULT 0, final_snapshot TEXT,
                report_expires_ms INTEGER NOT NULL DEFAULT 0,
                report_version TEXT NOT NULL DEFAULT '',
                report_kind TEXT NOT NULL DEFAULT '',
                report_count INTEGER NOT NULL DEFAULT 0,
                report_next_ms INTEGER NOT NULL DEFAULT 0,
                files INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS batches_order ON batches(timestamp,path);
            CREATE INDEX IF NOT EXISTS batches_schedule ON batches(state,retry_at);
            CREATE INDEX IF NOT EXISTS batches_cleanup ON batches(cleanup_pending,retry_at);
        """)
        source = str(config.source_dir)
        row = self.db.execute("SELECT value FROM settings WHERE key='source_dir'").fetchone()
        if row and row[0] != source:
            self.db.close()
            raise ValueError("工作目录绑定的 source_dir 不同，请使用新的工作目录")
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(batches)")}
        self.db.execute("BEGIN")
        for name, definition in (
            ("report_expires_ms", "INTEGER NOT NULL DEFAULT 0"),
            ("report_version", "TEXT NOT NULL DEFAULT ''"),
            ("report_kind", "TEXT NOT NULL DEFAULT ''"),
            ("report_count", "INTEGER NOT NULL DEFAULT 0"),
            ("report_next_ms", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                self.db.execute(f"ALTER TABLE batches ADD COLUMN {name} {definition}")
        # Adopt only retained results. Never resurrect acknowledged upload history.
        self.db.execute(
            "UPDATE batches SET report_expires_ms=timestamp+? "
            "WHERE (pending IS NOT NULL OR final_snapshot IS NOT NULL) "
            "AND report_expires_ms!=timestamp+?",
            (DEFERRED_REPORT_TTL_MS, DEFERRED_REPORT_TTL_MS),
        )
        self.db.execute(
            """UPDATE batches SET report_expires_ms=timestamp+?,
            report_version=lower(hex(randomblob(16))),
            report_kind=CASE WHEN state='done' THEN 'success' ELSE 'failure' END,
            report_count=0,report_next_ms=0
            WHERE report_version='' AND (pending IS NOT NULL OR final_snapshot IS NOT NULL)""",
            (DEFERRED_REPORT_TTL_MS,),
        )
        # A persisted random wait is never longer than five seconds, even after
        # a wall-clock adjustment while the previous process was running.
        self.db.execute(
            "UPDATE batches SET report_next_ms=? WHERE report_count>0 AND report_next_ms>?",
            (now_ms() + 5000, now_ms() + 5000),
        )
        # Older versions retained failure snapshots for additional rounds.
        # A failure already published once must never be replayed on upgrade.
        removed = self.db.execute(
            "UPDATE batches SET pending=NULL,final_snapshot=NULL,report_next_ms=0 "
            "WHERE report_kind='failure' AND report_count>=1 "
            "AND (pending IS NOT NULL OR final_snapshot IS NOT NULL)"
        ).rowcount
        if removed:
            logger.info("已取消历史失败结果的重复补发 batches=%s", removed)
        self.db.execute("INSERT OR IGNORE INTO settings VALUES ('source_dir',?)", (source,))
        self.db.commit()
        self.configure_batches(config)

    @staticmethod
    def target(config, path):
        return config.obs_path + (path.rstrip("/") + "/" if path != "." else "")

    def configure_batches(self, config):
        """Apply startup policy without touching completed upload history."""
        changed = 0
        with self.db:
            for batch in self.db.execute(
                "SELECT id,path,asset_path,report_enabled,task_type,collect_method "
                "FROM batches WHERE state!='done'"
            ):
                target = self.target(config, batch["path"])
                if target != batch["asset_path"]:
                    self.db.execute(
                        "UPDATE batches SET asset_path=?,pending=NULL,final_snapshot=NULL,"
                        "report_version='',report_kind='',report_count=0,report_next_ms=0 WHERE id=?",
                        (target, batch["id"]),
                    )
                    changed += 1
                    logger.info(
                        "OBS 目标变更 batch_id=%s old=%s new=%s；未完成批次将整批重试",
                        batch["id"],
                        batch["asset_path"],
                        target,
                    )
                if config.reports and not batch["report_enabled"]:
                    self.db.execute(
                        """UPDATE batches SET report_enabled=1,
                        task_type=?,collect_method=? WHERE id=?""",
                        (
                            batch["task_type"] or config.task_type,
                            batch["collect_method"] or config.collect_method,
                            batch["id"],
                        ),
                    )
        if changed:
            logger.info("OBS 目标已调整，受影响未完成批次：%s", changed)

    def add(self, path, config):
        asset_path = self.target(config, path)
        with self.db:
            self.db.execute(
                """INSERT OR IGNORE INTO batches
                (id,path,asset_path,timestamp,task_type,collect_method,report_enabled)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    path,
                    asset_path,
                    now_ms(),
                    config.task_type,
                    config.collect_method,
                    int(config.reports),
                ),
            )
        return self._decode(
            self.db.execute("SELECT * FROM batches WHERE path=?", (path,)).fetchone()
        )

    def get(self, batch_id):
        return self._decode(
            self.db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        )

    @staticmethod
    def _decode(row):
        result = dict(row)
        for key in ("error_info", "cleanup_error_info", "final_snapshot"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def rows(self):
        return [
            self._decode(r)
            for r in self.db.execute("SELECT * FROM batches ORDER BY timestamp,path")
        ]

    def paths(self):
        return (r[0] for r in self.db.execute("SELECT path FROM batches"))

    def candidates(self, excluded=(), limit=2, *, cleanup=False):
        excluded = tuple(excluded)
        clause = (
            "cleanup_pending=1"
            if cleanup
            else "state IN ('queued','failed','interrupted') AND cleanup_pending=0"
        )
        if excluded:
            clause += " AND id NOT IN (" + ",".join("?" for _ in excluded) + ")"
        return [
            dict(r)
            for r in self.db.execute(
                f"SELECT id,path FROM batches WHERE {clause} AND retry_at<=? "
                "ORDER BY timestamp,path LIMIT ?",
                (*excluded, time.time(), limit),
            )
        ]

    def outbox_next(self, excluded=(), after=None, *, due_ms=None):
        excluded = tuple(excluded)
        clause = "(pending IS NOT NULL OR final_snapshot IS NOT NULL)"
        args = []
        clause += " AND report_count<CASE WHEN report_kind='failure' THEN 1 ELSE ? END AND report_next_ms<=?"
        args.extend((RESULT_SEND_COUNT, now_ms() if due_ms is None else due_ms))
        if excluded:
            clause += " AND id NOT IN (" + ",".join("?" for _ in excluded) + ")"
            args.extend(excluded)
        if after is not None:
            clause += " AND (timestamp,path)>(?,?)"
            args.extend(after)
        row = self.db.execute(
            f"SELECT id FROM batches WHERE {clause} ORDER BY timestamp,path LIMIT 1", args
        ).fetchone()
        return self.get(row[0]) if row else None

    def summary(self):
        counts = {
            r[0]: r[1] for r in self.db.execute("SELECT state,COUNT(*) FROM batches GROUP BY state")
        }
        errors = []
        for row in self.db.execute(
            "SELECT path,state,error_info,cleanup_error_info FROM batches "
            "WHERE error_info IS NOT NULL OR cleanup_error_info IS NOT NULL "
            "ORDER BY timestamp DESC,path DESC LIMIT 50"
        ):
            for key in ("error_info", "cleanup_error_info"):
                if row[key]:
                    errors.append(
                        dict(path=row["path"], state=row["state"], error_info=json.loads(row[key]))
                    )
        return counts, errors[:50]

    def finals_to_prepare(self):
        return [
            self._decode(r)
            for r in self.db.execute(
                """SELECT * FROM batches WHERE state='done'
                AND final_snapshot IS NOT NULL AND pending IS NULL"""
            )
        ]

    def expire_reports(self):
        """Discard expired outbox data without changing successful upload history."""
        with self.db:
            expired = self.db.execute(
                """SELECT id,path,report_version,report_count FROM batches WHERE report_expires_ms>0 AND report_expires_ms<=?
                AND (pending IS NOT NULL OR final_snapshot IS NOT NULL)""",
                (now_ms(),),
            ).fetchall()
            self.db.executemany(
                "UPDATE batches SET pending=NULL,final_snapshot=NULL WHERE id=?",
                [(row["id"],) for row in expired],
            )
        for row in expired:
            logger.info(
                "补报已过期，首次入队已满 24 小时；保留批次记录 batch_id=%s path=%s version=%s sent=%s",
                row["id"],
                row["path"],
                row["report_version"],
                row["report_count"],
            )
        return [row["id"] for row in expired]

    def update(self, batch_id, **values):
        allowed = {
            "state",
            "attempts",
            "retry_at",
            "error",
            "error_info",
            "cleanup_error_info",
            "cleanup_pending",
            "pending",
            "files",
            "bytes",
            "final_snapshot",
            "report_expires_ms",
            "report_version",
            "report_kind",
            "report_count",
            "report_next_ms",
        }
        if not values.keys() <= allowed:
            raise ValueError("Invalid batch update")
        for key in ("error_info", "cleanup_error_info", "final_snapshot"):
            if values.get(key) is not None:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        with self.db:
            self.db.execute(
                "UPDATE batches SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?",
                (*values.values(), batch_id),
            )

    def acknowledge(self, batch_id, payload):
        """Legacy one-shot receipts must not acknowledge versioned results."""
        with self.db:
            result = self.db.execute(
                "UPDATE batches SET pending=NULL,final_snapshot=NULL "
                "WHERE id=? AND pending=? AND report_version=''",
                (batch_id, payload),
            )
        return result.rowcount > 0

    def save_result(self, batch_id, snapshot, kind):
        batch = self.get(batch_id)
        deadline = batch["timestamp"] + DEFERRED_REPORT_TTL_MS
        if now_ms() >= deadline:
            self.cancel_result(batch_id, reason="首次入队已满 24 小时")
            self.update(batch_id, report_expires_ms=deadline)
            logger.info("补报已过期，首次入队已满 24 小时 batch_id=%s", batch_id)
            return False
        # Several failure notifications can describe the same attempt.
        if batch["report_version"] and batch["report_kind"] == kind:
            return False
        self.update(
            batch_id,
            final_snapshot=snapshot,
            pending=None,
            report_version=str(uuid.uuid4()),
            report_kind=kind,
            report_count=0,
            report_next_ms=0,
            report_expires_ms=deadline,
        )
        return True

    def cancel_result(self, batch_id, *, reason):
        batch = self.get(batch_id)
        if batch["pending"] is not None or batch["final_snapshot"] is not None:
            logger.info(
                "取消结果补发 batch_id=%s version=%s reason=%s",
                batch_id,
                batch["report_version"],
                reason,
            )
        self.update(
            batch_id,
            pending=None,
            final_snapshot=None,
            report_version="",
            report_kind="",
            report_count=0,
            report_next_ms=0,
        )

    def acknowledge_result(self, receipt, next_ms):
        batch = self.get(receipt.batch_id)
        if batch["report_expires_ms"] and now_ms() >= batch["report_expires_ms"]:
            return False
        count = receipt.token.send_round
        limit = result_send_limit(batch["report_kind"])
        if count > limit:
            return False
        with self.db:
            result = self.db.execute(
                """UPDATE batches SET report_count=?,report_next_ms=?,
                pending=CASE WHEN ?=? THEN NULL ELSE pending END,
                final_snapshot=CASE WHEN ?=? THEN NULL ELSE final_snapshot END
                WHERE id=? AND report_version=? AND report_count=? AND pending=?""",
                (
                    count,
                    next_ms if count < limit else 0,
                    count,
                    limit,
                    count,
                    limit,
                    receipt.batch_id,
                    receipt.token.version,
                    count - 1,
                    receipt.payload,
                ),
            )
        return result.rowcount > 0

    def pending_results(self):
        return self.db.execute(
            "SELECT COUNT(*) FROM batches WHERE pending IS NOT NULL OR final_snapshot IS NOT NULL"
        ).fetchone()[0]

    def recover(self):
        with self.db:
            self.db.execute("""UPDATE batches SET state='queued',retry_at=0
                WHERE state IN ('copying','validating','uploading','interrupted','rejected','failed')""")

    def close(self):
        if self.on_close:
            self.on_close()
        self.db.close()
