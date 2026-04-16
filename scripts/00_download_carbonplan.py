#!/usr/bin/env python3
"""
Download CarbonPlan OCR wildfire burn probability raster for California.

Source  : source.coop/carbonplan/carbonplan-ocr
Dataset : USFS RDS-2020-0016-2 (scott-et-al-2024, 2nd Edition)
          Reprojected from EPSG:5070 → EPSG:4326, stored in Icechunk format.
Variable: BP — annual burn probability (probability a pixel burns in any given year)
Resolution: 30 m, EPSG:4326

Storage note:
  The Icechunk latitude coordinate is stored south→north (ascending).
  The script slices the CA window, then flips rows to produce a standard
  north-down GeoTIFF (negative y-resolution), which is the convention
  expected by rasterio, QGIS, and most downstream tools.

Output  : data/raw/wildfire_risk/carbonplan_burn_probability.tif
          Cloud-optimised GeoTIFF, tiled 512×512, LZW-compressed, float32.

Size estimate: ~4 GB of raw S3 chunk downloads → ~600 MB on disk after LZW.
Expected run time: 5–20 minutes depending on network speed.
"""

import os
import time

import icechunk
import numpy as np
import rasterio
import zarr
from rasterio.transform import from_origin

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BUCKET = "us-west-2.opendata.source.coop"
REGION = "us-west-2"
PREFIX = (
    "carbonplan/carbonplan-ocr/input/fire-risk/tensor/"
    "USFS/scott-et-al-2024/processed-30m-4326.icechunk"
)

OUTPUT_DIR  = "data/raw/wildfire_risk"
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "carbonplan_burn_probability.tif")

# California bounding box — USGS standard extent, EPSG:4326
CA_WEST  = -124.482003
CA_EAST  = -114.131211
CA_SOUTH =   32.528832
CA_NORTH =   42.009518


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def step(n: int, msg: str) -> None:
    print(f"\n{'='*60}\n{n} / 4  {msg}\n{'='*60}")


def elapsed(t0: float) -> str:
    s = int(time.time() - t0)
    return f"{s // 60}m {s % 60}s"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    t_total = time.time()

    # ------------------------------------------------------------------
    # 1. Open the Icechunk repository (anonymous, read-only)
    # ------------------------------------------------------------------
    step(1, "Opening CarbonPlan OCR Icechunk store")
    print(f"  s3://{BUCKET}/{PREFIX}")

    storage = icechunk.s3_storage(
        bucket=BUCKET,
        region=REGION,
        prefix=PREFIX,
        anonymous=True,
    )
    root = zarr.open(
        icechunk.Repository.open(storage).readonly_session("main").store,
        mode="r",
    )
    print(f"  Dataset version : {root.attrs.get('version', 'n/a')}")
    print(f"  BP shape        : {root['BP'].shape}  (CONUS, rows × cols)")

    # ------------------------------------------------------------------
    # 2. Load 1-D coordinates and find the California window
    # ------------------------------------------------------------------
    step(2, "Computing California slice indices")

    # Both arrays are 1-D pixel-centre coordinates.
    # latitude  : ascending  (22.43°N … 52.48°N, south → north)
    # longitude : ascending (-128.39°E … -64.05°E, west → east)
    lats    = root["latitude"][:]   # shape (97 579,)
    lons    = root["longitude"][:]  # shape (208 881,)
    lat_res = float(root["latitude"].attrs["resolution"])   # −0.000 308°
    lon_res = float(root["longitude"].attrs["resolution"])  # +0.000 308°

    # searchsorted on ascending arrays → first index inside each boundary
    row_start = int(np.searchsorted(lats, CA_SOUTH, side="left"))
    row_end   = int(np.searchsorted(lats, CA_NORTH, side="right"))
    col_start = int(np.searchsorted(lons, CA_WEST,  side="left"))
    col_end   = int(np.searchsorted(lons, CA_EAST,  side="right"))

    n_rows = row_end - row_start
    n_cols = col_end - col_start

    lat_ca = lats[row_start:row_end]   # ascending, southernmost first
    lon_ca = lons[col_start:col_end]

    print(f"  Lat window : {lat_ca[0]:.4f} → {lat_ca[-1]:.4f}°N  ({n_rows} rows)")
    print(f"  Lon window : {lon_ca[0]:.4f} → {lon_ca[-1]:.4f}°E  ({n_cols} cols)")
    print(f"  Raw data   : {n_rows * n_cols * 4 / 1e9:.2f} GB uncompressed")

    # ------------------------------------------------------------------
    # 3. Download the BP window from S3
    # ------------------------------------------------------------------
    step(3, "Downloading BP (burn probability) — California window")
    print("  Fetching chunks from S3 … (this may take several minutes)")

    t0  = time.time()
    bp  = root["BP"][row_start:row_end, col_start:col_end]   # triggers chunk fetches
    bp  = np.flipud(bp)   # flip to north-down convention for GeoTIFF

    print(f"  Done in {elapsed(t0)}")
    print(f"  Array shape : {bp.shape}  dtype={bp.dtype}")
    valid = bp[np.isfinite(bp)]
    print(f"  Value range : [{valid.min():.6f}, {valid.max():.6f}]  "
          f"({100 * np.isfinite(bp).mean():.1f}% valid pixels)")

    # ------------------------------------------------------------------
    # 4. Write Cloud-Optimised GeoTIFF
    # ------------------------------------------------------------------
    step(4, "Writing GeoTIFF")

    # After flipud, row 0 of bp corresponds to lat_ca[-1] (northernmost centre).
    # The top edge (north) of that pixel = centre + half a pixel upward.
    # lat_res is negative (−0.000 308), so: north = lat_ca[-1] − lat_res/2
    #                                               = lat_ca[-1] + 0.000 154
    north = lat_ca[-1] - lat_res / 2
    west  = lon_ca[0]  - lon_res / 2

    transform = from_origin(
        west=west,
        north=north,
        xsize=lon_res,        # positive pixel width
        ysize=-lat_res,       # positive pixel height (from_origin negates internally)
    )

    profile = {
        "driver":     "GTiff",
        "height":     n_rows,
        "width":      n_cols,
        "count":      1,
        "dtype":      "float32",
        "crs":        "EPSG:4326",
        "transform":  transform,
        "nodata":     float("nan"),
        "compress":   "lzw",
        "tiled":      True,
        "blockxsize": 512,
        "blockysize": 512,
        "bigtiff":    "IF_SAFER",
    }

    with rasterio.open(OUTPUT_PATH, "w", **profile) as dst:
        dst.write(bp.astype("float32"), 1)
        dst.update_tags(
            source="CarbonPlan OCR — source.coop/carbonplan/carbonplan-ocr",
            dataset="USFS RDS-2020-0016-2 (scott-et-al-2024, 2nd Edition)",
            variable="BP — annual burn probability",
            description=(
                "Probability that a given pixel burns in any single year. "
                "Clipped to California bounding box and stored north-down."
            ),
            resolution_m="30",
            crs="EPSG:4326",
            version=root.attrs.get("version", "2024-V2"),
            ca_bbox=f"W={CA_WEST} E={CA_EAST} S={CA_SOUTH} N={CA_NORTH}",
        )

    size_mb = os.path.getsize(OUTPUT_PATH) / 1e6
    print(f"  Saved : {OUTPUT_PATH}")
    print(f"  Size  : {size_mb:.1f} MB  (LZW-compressed)")
    print(f"\nTotal elapsed : {elapsed(t_total)}")
    print("Done.")


if __name__ == "__main__":
    main()
