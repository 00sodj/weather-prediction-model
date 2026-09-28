# -*- coding: utf-8 -*-
"""补采年度数据：只采缺失城市，小批次+长间隔避免429"""
import os, time, requests, pandas as pd
from datetime import datetime, timedelta, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weather_data")
CSV_PATH = os.path.join(DATA_DIR, "过去一年气温数据_逐日.csv")
DAILY_PARAMS = "temperature_2m_mean,temperature_2m_max,temperature_2m_min"
PARAM_CN = {
    "temperature_2m_mean": "日均气温(°C)",
    "temperature_2m_max": "日最高气温(°C)",
    "temperature_2m_min": "日最低气温(°C)",
}

from fetch_weather_data import CITIES

def main():
    df_exist = pd.read_csv(CSV_PATH)
    counts = df_exist.groupby("城市").size()
    incomplete = set(counts[counts < 60].index)
    all_cities = set(df_exist["城市"].unique())
    missing = set(c[0] for c in CITIES) - all_cities
    need_names = incomplete | missing
    need = [(n, la, lo) for n, la, lo in CITIES if n in need_names]
    print(f"总城市 {len(CITIES)}, 数据不足(<60天)或缺失: {len(need)} 个, 开始补采...")

    now = datetime.now(timezone.utc)
    end_date = (now - timedelta(days=5)).strftime("%Y-%m-%d")
    start_date = (now - timedelta(days=365)).strftime("%Y-%m-%d")

    new_rows = []
    batch_size = 5
    for i in range(0, len(need), batch_size):
        batch = need[i:i + batch_size]
        lats = ",".join(str(c[1]) for c in batch)
        lons = ",".join(str(c[2]) for c in batch)

        url = "https://archive-api.open-meteo.com/v1/archive"
        params = {
            "latitude": lats, "longitude": lons,
            "daily": DAILY_PARAMS,
            "start_date": start_date, "end_date": end_date,
            "timezone": "Asia/Shanghai",
        }

        names = [c[0] for c in batch]
        print(f"  [{i+1}-{min(i+batch_size,len(need))}/{len(need)}] {names}...", end=" ")

        ok = False
        for attempt in range(4):
            try:
                resp = requests.get(url, params=params, timeout=120)
                resp.raise_for_status()
                data = resp.json()
                ok = True
                break
            except Exception as e:
                wait = 10 * (attempt + 1)
                print(f"retry({attempt+1})...", end=" ")
                time.sleep(wait)

        if not ok:
            print("SKIP")
            continue

        results = data if isinstance(data, list) else [data]
        cnt = 0
        for j, city_data in enumerate(results):
            daily = city_data.get("daily", {})
            times = daily.get("time", [])
            for t_idx, ts in enumerate(times):
                row = {"城市": batch[j][0], "日期": ts}
                for p in DAILY_PARAMS.split(","):
                    vals = daily.get(p, [])
                    row[PARAM_CN.get(p, p)] = vals[t_idx] if t_idx < len(vals) else None
                new_rows.append(row)
                cnt += 1
        print(f"OK ({cnt} rows)")
        time.sleep(3)

    if new_rows:
        df_new = pd.DataFrame(new_rows)
        df_all = pd.concat([df_exist, df_new], ignore_index=True)
        # 补采区间会和已有数据重叠，不去重会让同一个 (城市, 日期) 出现多行
        df_all["日期"] = pd.to_datetime(df_all["日期"])
        df_all = df_all.drop_duplicates(subset=["城市", "日期"], keep="last")
        df_all = df_all.sort_values(["城市", "日期"])
        df_all["日期"] = df_all["日期"].dt.strftime("%Y-%m-%d")
        df_all.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
        print(f"\n合并完成: {df_all['城市'].nunique()} 城市, {len(df_all)} 条记录")
    else:
        print("无新数据")


if __name__ == "__main__":
    main()
