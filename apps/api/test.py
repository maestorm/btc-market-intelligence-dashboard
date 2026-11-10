from pathlib import Path
import duckdb

db_path = Path(__file__).resolve().parent / "data" / "market.duckdb"
print(f"Hledám databázi: {db_path}")

if not db_path.exists():
    print("Databáze zatím neexistuje. Nejdřív spusť API aplikaci.")
else:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        result = con.execute("""
            SELECT
                CAST(observed_at AS VARCHAR) AS observed_at,
                btc_usd,
                btc_usd_source,
                dvol
            FROM market_snapshots
            ORDER BY observed_at DESC
            LIMIT 10
        """)

        print("Sloupce:", [column[0] for column in result.description])

        rows = result.fetchall()
        if not rows:
            print("Tabulka zatím neobsahuje žádné záznamy.")
        else:
            for row in rows:
                print(row)
    finally:
        con.close()
