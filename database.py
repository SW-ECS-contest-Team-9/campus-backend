import os

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()


def conninfo(dbname: str | None = None) -> str:
    """.env 의 POSTGRES_* 로 접속 문자열을 만든다. 불러올 때 접속하지 않는다."""
    return psycopg.conninfo.make_conninfo(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ["POSTGRES_PORT"]),
        dbname=dbname or os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def connect(dbname: str | None = None) -> psycopg.Connection:
    return psycopg.connect(conninfo(dbname), row_factory=dict_row)


def get_conn():
    """FastAPI 의존성: 요청마다 연결 하나."""
    with connect() as conn:
        yield conn
