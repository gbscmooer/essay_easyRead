"""Local material identity migration; independent of research workflow tables."""

import json
import re


def migrate_material_integrity(engine):
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        if not conn.connection.driver_connection.in_transaction:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        if conn.exec_driver_sql("PRAGMA foreign_keys").scalar() and any(
            "AUTOINCREMENT"
            not in (
                conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE name=?", (table,)
                ).scalar()
                or ""
            ).upper()
            for table in ("papers", "workspaces")
        ):
            raise RuntimeError(
                "Material migration requires an isolated SQLite connection with foreign_keys disabled"
            )
        # Keep discarded orphan rows recoverable; do not touch report/loop assets.
        conn.exec_driver_sql("""CREATE TABLE IF NOT EXISTS material_orphan_archive (
            source_table TEXT NOT NULL, row_id INTEGER NOT NULL, payload TEXT NOT NULL,
            archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(source_table, row_id))""")
        high_water = {}
        for table in ("papers", "workspaces"):
            high_water[table] = conn.exec_driver_sql(
                f"SELECT COALESCE(MAX(id),0) FROM {table}"
            ).scalar()
        targets = {
            "notes": "paper_id",
            "annotations": "paper_id",
            "paper_tags": "paper_id",
            "analysis_results": "paper_id",
            "code_analyses": "paper_id",
            "workspace_papers": "paper_id",
            "paper_references": "citing_paper_id",
        }
        for table, column in targets.items():
            if not conn.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).first():
                continue
            high_water["papers"] = max(
                high_water["papers"],
                conn.exec_driver_sql(
                    f"SELECT COALESCE(MAX({column}),0) FROM {table}"
                ).scalar(),
            )
            where = f"{column} NOT IN (SELECT id FROM papers)"
            if table == "workspace_papers":
                high_water["workspaces"] = max(
                    high_water["workspaces"],
                    conn.exec_driver_sql(
                        "SELECT COALESCE(MAX(workspace_id),0) FROM workspace_papers"
                    ).scalar(),
                )
                where += " OR workspace_id NOT IN (SELECT id FROM workspaces)"
            rows = (
                conn.exec_driver_sql(f"SELECT * FROM {table} WHERE {where}")
                .mappings()
                .all()
            )
            for row in rows:
                conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO material_orphan_archive(source_table,row_id,payload) VALUES(?,?,?)",
                    (table, row["id"], json.dumps(dict(row), ensure_ascii=False)),
                )
            conn.exec_driver_sql(f"DELETE FROM {table} WHERE {where}")
        high_water["papers"] = max(
            high_water["papers"],
            conn.exec_driver_sql(
                "SELECT COALESCE(MAX(cited_paper_id),0) FROM paper_references"
            ).scalar(),
            conn.exec_driver_sql(
                "SELECT COALESCE(MAX(parent_paper_id),0) FROM papers"
            ).scalar(),
        )
        for table in ("research_runs", "research_graphs"):
            if conn.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).first():
                high_water["workspaces"] = max(
                    high_water["workspaces"],
                    conn.exec_driver_sql(
                        f"SELECT COALESCE(MAX(workspace_id),0) FROM {table}"
                    ).scalar(),
                )
        conn.exec_driver_sql(
            "UPDATE papers SET parent_paper_id=NULL WHERE parent_paper_id NOT IN (SELECT id FROM papers)"
        )
        conn.exec_driver_sql(
            "UPDATE paper_references SET cited_paper_id=NULL WHERE cited_paper_id NOT IN (SELECT id FROM papers)"
        )
        for table in ("papers", "workspaces"):
            _ensure_non_reused_ids(conn, table, high_water[table])


def _ensure_non_reused_ids(conn, table, high_water):
    ddl = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).scalar()
    if "AUTOINCREMENT" not in ddl.upper():
        # Preserve the original columns, defaults, constraints, indexes and triggers.
        # CREATE new -> copy -> drop -> rename, all in the same transaction.
        temp = f"material_migration_{table}"
        definition, count = re.subn(
            r"(?im)^\s*id\s+INTEGER\s+NOT\s+NULL\s*,",
            "\n id INTEGER PRIMARY KEY AUTOINCREMENT,",
            ddl,
            count=1,
        )
        if count != 1:
            raise RuntimeError(
                f"Unsupported {table} primary key; material migration aborted"
            )
        definition, count = re.subn(
            r",\s*PRIMARY KEY\s*\(id\)", "", definition, count=1, flags=re.I
        )
        if count != 1:
            raise RuntimeError(
                f"Unsupported {table} primary key; material migration aborted"
            )
        definition = re.sub(
            r"^CREATE TABLE\s+(?:\"\w+\"|\w+)",
            f'CREATE TABLE "{temp}"',
            definition,
            count=1,
            flags=re.I,
        )
        objects = (
            conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
                (table,),
            )
            .scalars()
            .all()
        )
        columns = [
            row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table}")')
        ]
        column_sql = ",".join(
            '"' + column.replace('"', '""') + '"' for column in columns
        )
        conn.exec_driver_sql(definition)
        conn.exec_driver_sql(
            f'INSERT INTO "{temp}" ({column_sql}) SELECT {column_sql} FROM "{table}"'
        )
        conn.exec_driver_sql(f'DROP TABLE "{table}"')
        conn.exec_driver_sql(f'ALTER TABLE "{temp}" RENAME TO "{table}"')
        for statement in objects:
            conn.exec_driver_sql(statement)
    # Also reserve IDs found in old orphan rows, before those rows were archived.
    if conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_sequence WHERE name=?", (table,)
    ).first():
        conn.exec_driver_sql(
            "UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name=?",
            (high_water, table),
        )
    else:
        conn.exec_driver_sql(
            "INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)", (table, high_water)
        )
