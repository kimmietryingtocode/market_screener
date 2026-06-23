import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup


URL = "https://finance.yahoo.com/sectors/"
OUT_PATH = Path("data/raw/yahoo/yahoo_sector_table.csv")


def parse_percent(value: str) -> float:
    """
    Converts strings like '32.14%' or '+9.63%' into decimals.
    Example:
        '9.63%' -> 0.0963
        '-2.10%' -> -0.021
    """
    if value is None:
        return float("nan")

    value = (
        value.strip()
        .replace("%", "")
        .replace(",", "")
        .replace("+", "")
    )

    if value in {"", "-", "N/A", "nan"}:
        return float("nan")

    return float(value) / 100.0


def fetch_html(url: str = URL, retries: int = 3, sleep_seconds: float = 2.0) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code == 200:
                return response.text

            last_error = RuntimeError(
                f"Yahoo returned status code {response.status_code}"
            )

        except requests.RequestException as exc:
            last_error = exc

        print(f"Attempt {attempt} failed. Retrying...")
        time.sleep(sleep_seconds)

    raise RuntimeError(f"Could not fetch Yahoo sectors page: {last_error}")


def parse_sector_table(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")

    section = soup.find("section", {"data-testid": "sector-listing"})

    if section is None:
        raise ValueError(
            "Could not find Yahoo sector-listing section. "
            "Yahoo may have changed the page structure."
        )

    table = section.find("table")

    if table is None:
        raise ValueError(
            "Could not find sector table inside sector-listing section."
        )

    rows = []

    for tr in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]

        if not cells:
            continue

        if cells[0].lower() == "sector":
            continue

        if len(cells) < 3:
            continue

        sector = cells[0]
        market_weight_raw = cells[1]
        ytd_return_raw = cells[2]

        rows.append(
            {
                "sector": sector,
                "market_weight": parse_percent(market_weight_raw),
                "ytd_return": parse_percent(ytd_return_raw),
                "market_weight_raw": market_weight_raw,
                "ytd_return_raw": ytd_return_raw,
            }
        )

    if not rows:
        raise ValueError(
            "No sector rows found. Yahoo may be rendering the table differently."
        )

    df = pd.DataFrame(rows)

    # Remove duplicate rows if Yahoo includes repeated layout sections.
    df = df.drop_duplicates(subset=["sector"]).reset_index(drop=True)

    return df


def scrape_yahoo_sector_table() -> pd.DataFrame:
    html = fetch_html(URL)
    df = parse_sector_table(html)
    return df


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    df = scrape_yahoo_sector_table()
    df.to_csv(OUT_PATH, index=False)

    print(df)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()