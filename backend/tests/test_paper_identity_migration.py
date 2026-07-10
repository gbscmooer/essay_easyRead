"""论文身份迁移回填、冲突与幂等性测试。"""

from __future__ import annotations

import json

from sqlmodel import create_engine

from backend.services.database import _migrate_schema


def test_identity_migration_backfills_legacy_rows_idempotently(tmp_path):
    database_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE papers (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                pdf_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                title TEXT,
                status TEXT,
                external_id TEXT
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO papers
                (id, filename, pdf_path, file_size, title, status, external_id)
            VALUES
                (1, 'one.pdf', '', 0, 'Legacy One', 'pending', 'DOI:10.5555/legacy'),
                (2, 'two.pdf', '', 0, 'Legacy Duplicate', 'pending', 'doi:10.5555/legacy'),
                (3, 'three.pdf', '', 0, 'Legacy OpenAlex', 'pending', 'https://openalex.org/W123')
            """
        )
        conn.commit()

    _migrate_schema(engine)
    _migrate_schema(engine)

    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            """
            SELECT id, doi, openalex_id, canonical_id, title_normalized, source_ids
            FROM papers ORDER BY id
            """
        ).fetchall()
        indexes = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA index_list(papers)").fetchall()
        }

    assert rows[0][1] == "10.5555/legacy"
    assert rows[0][3] == "doi:10.5555/legacy"
    assert rows[0][4] == "legacy one"
    assert json.loads(rows[0][5])["doi"] == "10.5555/legacy"

    # 冲突只回填标识，不自动 merge，也不写第二个相同强 canonical_id。
    assert rows[1][1] == "10.5555/legacy"
    assert rows[1][3] is None

    assert rows[2][2] == "W123"
    assert rows[2][3] == "openalex:W123"
    assert "ux_papers_canonical_strong" in indexes

    conflict_report = json.loads(
        (tmp_path / "migration_identity_conflicts.json").read_text(encoding="utf-8")
    )
    assert conflict_report["conflicts"] == [
        {
            "paper_id": 2,
            "canonical_id": "doi:10.5555/legacy",
            "owner_id": 1,
        }
    ]
