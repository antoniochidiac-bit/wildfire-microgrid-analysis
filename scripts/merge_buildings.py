"""
Merge 215 CA building tile files (GeoJSONL CSV.gz) into a single GeoParquet.
Uses chunked writing to stay under 4 GB peak memory.
"""
import gzip, json, sys
from pathlib import Path
import geopandas as gpd
from shapely.geometry import shape, box
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

TILES_DIR = Path("data/raw/buildings/tiles")
OUT       = Path("data/raw/buildings/ca_buildings.parquet")
SHARDS    = Path("data/raw/buildings/_shards")
SHARDS.mkdir(exist_ok=True)

# CA strict clip box
ca_box   = box(-124.5, 32.5, -114.1, 42.0)
BATCH    = 20   # tiles per shard

tiles = sorted(TILES_DIR.glob("*.csv.gz"))
print(f"Merging {len(tiles)} tiles in batches of {BATCH}...", flush=True)

def process_batch(batch_tiles, shard_idx):
    geoms, heights, confs = [], [], []
    for t in batch_tiles:
        with gzip.open(t, 'rt') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    feat   = json.loads(line)
                    props  = feat.get('properties', {})
                    geom   = shape(feat['geometry'])
                    if geom.intersects(ca_box):
                        geoms.append(geom)
                        heights.append(props.get('height', -1.0))
                        confs.append(props.get('confidence', -1.0))
                except Exception:
                    pass
    if not geoms:
        return 0
    gdf = gpd.GeoDataFrame({'height': heights, 'confidence': confs, 'geometry': geoms},
                           crs='EPSG:4326')
    shard_path = str(SHARDS / f"shard_{shard_idx:04d}.parquet")
    gdf.to_parquet(shard_path, index=False)
    return len(gdf)

total = 0
for i in range(0, len(tiles), BATCH):
    batch = tiles[i:i+BATCH]
    shard_idx = i // BATCH
    shard_path = SHARDS / f"shard_{shard_idx:04d}.parquet"
    if shard_path.exists():
        # Count existing shard
        existing = pq.read_metadata(str(shard_path)).num_rows
        total += existing
        print(f"  Batch {shard_idx+1}: {existing:,} features (already exists)", flush=True)
        continue
    n = process_batch(batch, shard_idx)
    total += n
    print(f"  Batch {shard_idx+1}/{(len(tiles)+BATCH-1)//BATCH}: {n:,} features  (running total {total:,})", flush=True)

print(f"\nAll batches done. Total features: {total:,}", flush=True)

# Concatenate shards into final parquet using pyarrow (no geometry loading)
print("Concatenating shards...", flush=True)
shard_files = sorted(SHARDS.glob("shard_*.parquet"))
writer = None
rows_written = 0
for sf in shard_files:
    table = pq.read_table(str(sf))
    if writer is None:
        writer = pq.ParquetWriter(str(OUT), table.schema, compression='snappy')
    writer.write_table(table)
    rows_written += len(table)

if writer:
    writer.close()

print(f"Saved: {OUT}  ({OUT.stat().st_size/1e6:.0f} MB, {rows_written:,} rows)", flush=True)

# Clean up shards
import shutil
shutil.rmtree(str(SHARDS))
print("Shards cleaned up.", flush=True)
