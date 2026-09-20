import pandas as pd

from screener.pipeline.factset_export import select_review_queue


def test_review_queue_enforces_sector_and_industry_caps() -> None:
    rows = []
    for rank in range(12):
        rows.append(
            {
                "ticker": f"T{rank:02d}",
                "company": f"Company {rank}",
                "sector": "A" if rank < 6 else ("B" if rank < 10 else "C"),
                "industry": f"I{rank // 2}",
                "score": 100 - rank,
            }
        )
    config = {
        "company_scan": {
            "review_queue": {
                "top_n": 10,
                "max_per_sector": 3,
                "max_per_industry": 2,
            }
        }
    }

    queue = select_review_queue(pd.DataFrame(rows), config)

    assert len(queue) == 8
    assert queue["sector"].value_counts().max() == 3
    assert queue["industry"].value_counts().max() == 2
    assert list(queue["recommendation_rank"]) == list(range(1, 9))
