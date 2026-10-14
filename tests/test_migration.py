import psycopg
import pytest
from alembic import command

import database
from conftest import alembic_cfg

TABLES = {"campus", "map_release", "building", "floor", "node", "edge", "place", "walk_area", "walk_area_node"}


def tables():
    with database.connect() as c:
        rows = c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
    return {r["tablename"] for r in rows}


def test_upgrade_downgrade_upgrade():
    assert TABLES <= tables()
    command.downgrade(alembic_cfg(), "0001_map_schema")
    with database.connect() as c:
        cols = {r["column_name"] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'node'").fetchall()}
    assert "building_id" not in cols
    command.downgrade(alembic_cfg(), "base")
    assert not (TABLES & tables())
    command.upgrade(alembic_cfg(), "head")
    assert TABLES <= tables()


def test_published_needs_file(clean):
    c = clean
    cid = c.execute("INSERT INTO campus (code, name) VALUES ('t', 't') RETURNING id").fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        c.execute("INSERT INTO map_release (campus_id, version, schema_version, status) VALUES (%s, 1, 1, 'published')",
                  (cid,))
    c.rollback()


def test_indoor_node_needs_building(clean):
    c = clean
    cid = c.execute("INSERT INTO campus (code, name) VALUES ('t', 't') RETURNING id").fetchone()["id"]
    rid = c.execute("INSERT INTO map_release (campus_id, version, schema_version) VALUES (%s, 1, 1) RETURNING id",
                    (cid,)).fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        c.execute("INSERT INTO node (release_id, key, location, x, y, z) VALUES (%s, 'n', 'indoor', 0, 0, 0)", (rid,))
    c.rollback()
