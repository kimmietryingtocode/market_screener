"""Weekly market screen: sectors -> industries -> companies -> FactSet list."""

from pathlib import Path

import yaml

from .pipeline import company_scan, factset_export, industry_scan, sector_scan

REPO_ROOT = Path(__file__).resolve().parent.parent


def run(config_path: Path) -> Path:
    """Run the sweep and return the immutable screen output directory."""
    config = yaml.safe_load(config_path.read_text())

    print("Stage 1/4: scanning sectors...")
    sectors = sector_scan.scan_sectors(config)
    selected_sectors = sectors[sectors["selected"]]
    print(sectors[["sector_etf", "sector", "return_3m", "rs_3m", "score", "selected"]]
          .to_string(index=False, float_format="%.3f"))

    print("\nStage 2/4: scanning industries within "
          f"{', '.join(selected_sectors['sector'])}...")
    industries = industry_scan.scan_industries(selected_sectors, config)
    selected_industries = industries[industries["selected"]]
    print(selected_industries[["sector", "industry", "return_3m", "rs_3m", "score"]]
          .to_string(index=False, float_format="%.3f"))

    print("\nStage 3/4: screening companies in "
          f"{len(selected_industries)} industries...")
    shortlist = company_scan.scan_companies(selected_industries, config)
    print(shortlist[["ticker", "company", "industry", "return_3m", "rs_3m", "score"]]
          .to_string(index=False, float_format="%.3f"))

    print("\nStage 4/4: writing FactSet lookup list...")
    out_dir = factset_export.write_exports(
        shortlist,
        sectors,
        industries,
        REPO_ROOT / config["output"]["directory"],
        config,
    )
    print(f"\n{len(shortlist)} companies to research. Files in {out_dir}/:")
    for path in sorted(out_dir.iterdir()):
        print(f"  {path.name}")

    queue = factset_export.select_review_queue(shortlist, config)
    print("\nTop 10 for FactSet review:")
    print(queue[["ticker", "company", "sector", "industry", "score"]]
          .to_string(index=False, float_format="%.3f"))
    return out_dir
