from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

import database
from main import app


@pytest.fixture
def ads(conn):
    conn.execute("TRUNCATE advertisements RESTART IDENTITY")
    yield conn
    conn.rollback()


def insert(conn, *, starts=None, ends=None, active=True):
    return conn.execute(
        """INSERT INTO advertisements (title, image_url, target_url, starts_at, ends_at, is_active)
        VALUES ('행사', '/banner.png', 'https://example.com/event', %s, %s, %s) RETURNING id""",
        (starts, ends, active),
    ).fetchone()["id"]


def test_active_window_boundaries_and_order(ads):
    from advertisements import repository

    now = ads.execute("SELECT now() AS now").fetchone()["now"]
    later = now + timedelta(days=1)
    earlier = now - timedelta(days=1)
    expected = [insert(ads), insert(ads, starts=now, ends=later), insert(ads, ends=later)]
    insert(ads, active=False)
    insert(ads, starts=later)
    insert(ads, starts=earlier, ends=now)
    insert(ads, ends=earlier)
    assert [ad["id"] for ad in repository.list_active(ads)] == expected


def test_api_contract_and_empty_result(ads):
    def connection():
        yield ads

    app.dependency_overrides[database.get_conn] = connection
    try:
        with TestClient(app) as client:
            response = client.get("/v1/advertisements")
            assert response.status_code == 200
            assert response.json() == {"count": 0, "items": []}
            start = datetime(2020, 1, 1, tzinfo=timezone(timedelta(hours=9)))
            identifier = insert(ads, starts=start)
            insert(ads, active=False)
            response = client.get("/v1/advertisements")
            assert response.status_code == 200
            body = response.json()
            assert body["count"] == 1
            item = body["items"][0]
            assert item == {
                "id": identifier,
                "title": "행사",
                "imageUrl": "/banner.png",
                "targetUrl": "https://example.com/event",
                "startsAt": item["startsAt"],
                "endsAt": None,
                "isActive": True,
            }
            assert datetime.fromisoformat(item["startsAt"]) == start
    finally:
        del app.dependency_overrides[database.get_conn]


@pytest.mark.parametrize("delta", [0, -1])
def test_invalid_period_rejected(ads, delta):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(psycopg.errors.CheckViolation):
        insert(ads, starts=start, ends=start + timedelta(seconds=delta))


def test_new_ads_inactive_by_default(ads):
    row = ads.execute(
        """INSERT INTO advertisements (title, image_url, target_url)
        VALUES ('행사', '/banner.png', 'https://example.com') RETURNING is_active"""
    ).fetchone()
    assert row["is_active"] is False
