from psycopg import Connection


def list_active(conn: Connection) -> list[dict]:
    """시작 포함·종료 제외. 별도 우선순위 도입 전에는 id가 작은 광고부터 노출한다."""
    return conn.execute(
        """SELECT id, title, image_url, target_url, starts_at, ends_at, is_active
        FROM advertisements
        WHERE is_active = true
          AND (starts_at IS NULL OR starts_at <= now())
          AND (ends_at IS NULL OR now() < ends_at)
        ORDER BY id"""
    ).fetchall()
