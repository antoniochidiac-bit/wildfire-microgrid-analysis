import urllib.request, urllib.parse, json, geopandas as gpd
from pathlib import Path
import pandas as pd

CA_WHERE = urllib.parse.quote("STATE='CA'")

# ── 1. Download CA hospitals ──────────────────────────────────────────────
org_h = "XG15cJAlne2vxtgt"
base_h = f"https://services.arcgis.com/{org_h}/arcgis/rest/services/Hospitals_hifld/FeatureServer/0"
h_fields = urllib.parse.quote("OBJECTID_1,NAME,ADDRESS,CITY,STATE,ZIP,COUNTY,TYPE,STATUS,BEDS,TRAUMA,HELIPAD,LATITUDE,LONGITUDE")
all_h = []
offset = 0
while True:
    url = (f"{base_h}/query?where={CA_WHERE}"
           f"&outFields={h_fields}&orderByFields=OBJECTID_1+ASC"
           f"&resultOffset={offset}&resultRecordCount=500&f=geojson")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    feats = data.get("features", [])
    if not feats:
        break
    all_h.extend(feats)
    if len(feats) < 500:
        break
    offset += 500

gdf_h = gpd.GeoDataFrame.from_features(all_h, crs="EPSG:4326")
gdf_h["facility_type"] = "Hospital"
print(f"Hospitals: {len(gdf_h)}")

# ── 2. Download CA fire stations ──────────────────────────────────────────
org_f = "0MSEUqKaxRlEPj5g"
base_f = f"https://services1.arcgis.com/{org_f}/arcgis/rest/services/Fire_Stations2/FeatureServer/0"
f_fields = urllib.parse.quote("OBJECTID_1,NAME,ADDRESS,CITY,STATE,ZIP,COUNTY,TYPE,SPECIALTY,EMS,FDID,TOTALPERS,NUMTRKS,TOTAL_VEHI")
all_f = []
offset = 0
while True:
    url = (f"{base_f}/query?where={CA_WHERE}"
           f"&outFields={f_fields}&orderByFields=OBJECTID_1+ASC"
           f"&resultOffset={offset}&resultRecordCount=500&f=geojson")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    feats = data.get("features", [])
    if not feats:
        break
    all_f.extend(feats)
    print(f"  {len(all_f)} fire stations...", flush=True)
    if len(feats) < 500:
        break
    offset += 500

gdf_f = gpd.GeoDataFrame.from_features(all_f, crs="EPSG:4326")
gdf_f["facility_type"] = "Fire Station"
print(f"Fire Stations: {len(gdf_f)}")

# ── 3. Combine and save ───────────────────────────────────────────────────
keep = ["NAME", "ADDRESS", "CITY", "STATE", "ZIP", "COUNTY", "TYPE", "facility_type", "geometry"]
for col in keep[:-1]:
    if col not in gdf_h.columns:
        gdf_h[col] = None
    if col not in gdf_f.columns:
        gdf_f[col] = None

combined = pd.concat([gdf_h[keep], gdf_f[keep]], ignore_index=True)
gdf = gpd.GeoDataFrame(combined, crs="EPSG:4326")

out = Path("data/raw/microgrids/critical_facilities.gpkg")
out.parent.mkdir(parents=True, exist_ok=True)
gdf.to_file(str(out), driver="GPKG")
print(f"\nSaved: {out}  ({out.stat().st_size/1e3:.0f} KB)")
print(f"Total: {len(gdf)}")
print(gdf["facility_type"].value_counts().to_string())
print(gdf[["NAME","CITY","COUNTY","facility_type"]].head(5).to_string())
