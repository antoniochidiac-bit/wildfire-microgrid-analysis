"""
Download Microsoft Building Footprints for California.
Files are CSV.gz tiles identified by Bing Maps quadkeys (zoom 9).
Saves individual tiles to data/raw/buildings/tiles/
then merges into data/raw/buildings/ca_buildings.parquet
"""

import urllib.request, csv, io, math, os, sys, time, gzip
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TILES_DIR    = PROJECT_ROOT / "data/raw/buildings/tiles"
OUT_PARQUET  = PROJECT_ROOT / "data/raw/buildings/ca_buildings.parquet"
TILES_DIR.mkdir(parents=True, exist_ok=True)

CATALOG_URL = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"

# CA bounding box (generous — tiles may extend slightly outside)
CA_W, CA_S, CA_E, CA_N = -124.5, 32.5, -114.1, 42.0

# ── quadkey helpers ────────────────────────────────────────────────────────
def quadkey_to_tile(qk):
    zoom = len(qk)
    tx, ty = 0, 0
    for ch in qk:
        tx <<= 1; ty <<= 1
        if ch == '1':   tx |= 1
        elif ch == '2': ty |= 1
        elif ch == '3': tx |= 1; ty |= 1
    return tx, ty, zoom

def tile_bbox(tx, ty, zoom):
    n = 2 ** zoom
    w = tx / n * 360 - 180
    e = (tx + 1) / n * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty+1) / n))))
    return w, south, e, north

def intersects_ca(bbox):
    w, s, e, n = bbox
    return not (e < CA_W or w > CA_E or n < CA_S or s > CA_N)

# ── catalog ────────────────────────────────────────────────────────────────
def get_ca_tiles():
    print("Fetching tile catalog...", flush=True)
    with urllib.request.urlopen(CATALOG_URL) as r:
        content = r.read().decode('utf-8')
    rows = list(csv.DictReader(io.StringIO(content)))
    us = [r for r in rows if r['Location'] == 'UnitedStates']
    ca = []
    for r in us:
        qk = r['QuadKey']
        bbox = tile_bbox(*quadkey_to_tile(qk))
        if intersects_ca(bbox):
            ca.append({'qk': qk, 'url': r['Url'], 'size': r['Size'], 'bbox': bbox})
    print(f"Found {len(ca)} CA tiles", flush=True)
    return ca

# ── download ───────────────────────────────────────────────────────────────
_print_lock = threading.Lock()

def download_tile(tile, idx, total):
    qk   = tile['qk']
    url  = tile['url']
    dest = TILES_DIR / f"{qk}.csv.gz"
    if dest.exists() and dest.stat().st_size > 1000:
        with _print_lock:
            print(f"  [{idx}/{total}] {qk} already exists ({tile['size']}), skipping", flush=True)
        return dest, True

    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            dest.write_bytes(data)
            with _print_lock:
                print(f"  [{idx}/{total}] {qk}  {tile['size']}  downloaded", flush=True)
            return dest, False
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                with _print_lock:
                    print(f"  [{idx}/{total}] {qk}  FAILED: {e}", flush=True)
                return dest, False
    return dest, False

# ── merge ──────────────────────────────────────────────────────────────────
def merge_to_parquet(tile_files):
    import pandas as pd
    import geopandas as gpd
    from shapely import wkt

    print(f"\nMerging {len(tile_files)} tiles into GeoParquet...", flush=True)
    dfs = []
    for i, fp in enumerate(tile_files):
        if not fp.exists() or fp.stat().st_size < 100:
            continue
        try:
            df = pd.read_csv(fp, compression='gzip')
            dfs.append(df)
            if (i+1) % 20 == 0:
                print(f"  Read {i+1}/{len(tile_files)} tiles...", flush=True)
        except Exception as e:
            print(f"  Warning: could not read {fp.name}: {e}", flush=True)

    if not dfs:
        print("No data to merge!", flush=True)
        return

    merged = pd.concat(dfs, ignore_index=True)
    print(f"Total rows before clip: {len(merged):,}", flush=True)

    # Detect geometry column
    geom_col = None
    for col in merged.columns:
        if col.lower() in ('geometry', 'wkt', 'geom'):
            geom_col = col
            break
    if geom_col:
        merged[geom_col] = merged[geom_col].apply(wkt.loads)
        gdf = gpd.GeoDataFrame(merged, geometry=geom_col, crs='EPSG:4326')
    elif 'latitude' in merged.columns and 'longitude' in merged.columns:
        gdf = gpd.GeoDataFrame(merged,
              geometry=gpd.points_from_xy(merged['longitude'], merged['latitude']),
              crs='EPSG:4326')
    else:
        print(f"Columns: {list(merged.columns)}", flush=True)
        print("Cannot identify geometry column — saving as CSV instead", flush=True)
        merged.to_csv(str(OUT_PARQUET).replace('.parquet', '.csv'), index=False)
        return

    # Clip to CA bbox
    ca_box = (-124.5, 32.5, -114.1, 42.0)
    from shapely.geometry import box
    gdf = gdf.clip(box(*ca_box))
    print(f"Rows after CA clip: {len(gdf):,}", flush=True)
    gdf.to_parquet(str(OUT_PARQUET), index=False)
    print(f"Saved: {OUT_PARQUET}  ({OUT_PARQUET.stat().st_size/1e6:.1f} MB)", flush=True)

# ── main ───────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    tiles = get_ca_tiles()
    total = len(tiles)

    # Parallel download with 8 workers
    downloaded = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(download_tile, t, i+1, total): t
                   for i, t in enumerate(tiles)}
        for fut in as_completed(futures):
            path, skipped = fut.result()
            downloaded.append(path)

    print(f"\nDownload complete: {len(downloaded)} files in {TILES_DIR}", flush=True)

    # Peek at one file to understand schema
    existing = [p for p in downloaded if p.exists() and p.stat().st_size > 100]
    if existing:
        import pandas as pd
        sample = pd.read_csv(existing[0], compression='gzip', nrows=3)
        print(f"\nSample columns from {existing[0].name}:")
        print(sample.dtypes)
        print(sample.head(2).to_string())
        print(flush=True)

    # Merge
    if '--no-merge' not in sys.argv:
        merge_to_parquet(existing)
    else:
        print("Skipping merge (--no-merge flag set)", flush=True)
