"""실제 Postgres(compose 의 postgres)에 시험용 DB 를 만들어 쓴다."""

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
TEST_DB = os.environ.get("POSTGRES_TEST_DB", "campus_test")
MAIN_DB = os.environ["POSTGRES_DB"]
os.environ["POSTGRES_DB"] = TEST_DB  # 아래 모든 접속(앱·alembic)이 시험 DB 로

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

import database  # noqa: E402


def alembic_cfg() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


@pytest.fixture(scope="session", autouse=True)
def test_db(tmp_path_factory):
    os.environ["MAP_DATA_DIR"] = str(tmp_path_factory.mktemp("releases"))
    with psycopg.connect(database.conninfo(MAIN_DB), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{TEST_DB}"')
    command.upgrade(alembic_cfg(), "head")
    yield
    with psycopg.connect(database.conninfo(MAIN_DB), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')


@pytest.fixture
def conn():
    with database.connect() as c:
        yield c


@pytest.fixture
def clean(conn):
    from mapdata import store

    conn.execute("TRUNCATE campus RESTART IDENTITY CASCADE")
    conn.commit()
    store.clear()
    yield conn
