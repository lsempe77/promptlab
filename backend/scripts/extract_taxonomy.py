"""Extract controlled vocabularies (countries, sector/sub-sector taxonomy) from the
DEP extraction protocol workbook's 'Lists' sheet into a JSON file the scorer/prompt
builder can load without needing pandas/openpyxl at request time.
"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
XLSX = ROOT / "DEP extraction protocol for IE v4- Admin panel.xlsx"
OUT = Path(__file__).resolve().parents[1] / "app" / "data" / "taxonomy.json"

df = pd.ExcelFile(XLSX).parse("Lists", header=None)
header = df.iloc[2].tolist()

countries = [v for v in df.iloc[3:, 2].tolist() if isinstance(v, str) and v.strip()]

# WB_Sectors block starts at the column labeled "WB_Sectors"; the following 11
# columns (one per sector) each contain that sector's sub-sector list underneath it.
sector_col = header.index("WB_Sectors")
sector_names = [h.strip("_").replace("_", " ") for h in header[sector_col + 1: sector_col + 12]]
sub_sectors = {}
for offset, name in enumerate(sector_names):
    col = sector_col + 1 + offset
    values = [v for v in df.iloc[3:, col].tolist() if isinstance(v, str) and v.strip()]
    sub_sectors[name] = values

taxonomy = {
    "countries": countries,
    "sectors": sector_names,
    "sub_sectors_by_sector": sub_sectors,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(taxonomy, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Wrote {OUT}")
print(f"  countries: {len(countries)}")
print(f"  sectors: {sector_names}")
for k, v in sub_sectors.items():
    print(f"  {k}: {len(v)} sub-sectors")
