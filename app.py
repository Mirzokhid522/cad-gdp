import io
import zipfile
import pandas as pd
import requests
from flask import Flask, jsonify, render_template

app = Flask(__name__)

STATCAN_GDP_URL = "https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/36100434/en"
CACHED_DATA = None

def load_and_cache_data():
    global CACHED_DATA
    try:
        print("-> [Startup] Requesting download link for GDP table from StatCan API...")
        response = requests.get(STATCAN_GDP_URL, timeout=30)
        response.raise_for_status()
        api_result = response.json()

        csv_zip_url = api_result.get("object")
        if not csv_zip_url:
            print(f"[ERROR] StatCan API response missing 'object': {api_result}")
            return

        print("-> [Startup] Downloading bulk GDP CSV zip from StatCan...")
        zip_resp = requests.get(csv_zip_url, timeout=120)
        zip_resp.raise_for_status()

        print("-> [Startup] Extracting zip archive in memory...")
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
            csv_filename = [name for name in z.namelist() if name.endswith(".csv") and "sub" not in name][0]
            
            use_cols = ["REF_DATE", "GEO", "North American Industry Classification System (NAICS)", "VALUE"]
            
            print("-> [Startup] Processing GDP CSV in low-memory chunks...")
            gdp_chunks = []
            
            with z.open(csv_filename) as f:
                for chunk in pd.read_csv(f, usecols=use_cols, dtype={"VALUE": "float32"}, chunksize=100000, low_memory=True):
                    chunk["REF_DATE"] = pd.to_datetime(chunk["REF_DATE"])
                    
                    mask = (
                        (chunk["GEO"] == "Canada") &
                        (chunk["REF_DATE"] >= "2025-01-01") &
                        (chunk["North American Industry Classification System (NAICS)"].str.contains("All industries", case=False, na=False))
                    )
                    sub = chunk[mask]
                    if not sub.empty:
                        gdp_chunks.append(sub)

        if not gdp_chunks:
            print("[ERROR] GDP filtering resulted in 0 rows!")
            return

        gdp_df = pd.concat(gdp_chunks, ignore_index=True)
        
        # Aggregate by date in case of multiple pricing formats/units
        gdp_agg = gdp_df.groupby("REF_DATE")["VALUE"].first().reset_index()
        gdp_agg["Month"] = gdp_agg["REF_DATE"].dt.strftime("%b %Y")
        gdp_agg = gdp_agg.sort_values("REF_DATE")

        CACHED_DATA = {
            "months": gdp_agg["Month"].tolist(),
            "gdp": gdp_agg["VALUE"].tolist(),
        }
        print("-> [Startup] GDP data successfully processed and cached!")
    except Exception as e:
        print(f"[CRITICAL ERROR during startup cache]: {e}")

# Pre-fetch data immediately on server boot
load_and_cache_data()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/labor-data")
def get_labor_data():
    if CACHED_DATA is None:
        load_and_cache_data()
    
    if CACHED_DATA is None:
        return jsonify({"error": "GDP data failed to initialize. Check Render logs."}), 500
        
    return jsonify(CACHED_DATA)

if __name__ == "__main__":
    app.run(debug=True, port=5076)