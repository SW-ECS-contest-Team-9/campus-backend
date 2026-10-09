import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
with psycopg.connect(
    host="127.0.0.1",
    port=int(os.environ["POSTGRES_PORT"]),
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
) as conn:
    result = conn.execute("SELECT 1").fetchone()
    print(result)
