# run_all_parallel.py
# python run_all_parallel.py 20260824
""" 全部数据转换 
用法: python run_all_parallel.py 20260824
"""
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

if len(sys.argv) < 2:
    print("❌ 请指定日期")
    print("用法: python run_all_parallel.py YYYYMMDD")
    sys.exit(1)

date_day = sys.argv[1]
date_month = date_day[:6]

print(f"📅 日期: {date_day}")
print(f"📅 GFEX月份: {date_month}")

commands = [
    f"python scripts/convert_czce.py --date {date_day}",
    f"python scripts/convert_dce_xlsx.py --date {date_day}",
    f"python scripts/convert_cffex.py --date {date_day}",
    f"python scripts/convert_gfex.py --date {date_month}",
]

def run(cmd):
    print(f"▶️  {cmd}")
    subprocess.run(cmd, shell=True)

with ThreadPoolExecutor(max_workers=4) as executor:
    executor.map(run, commands)

print("✅ 转换完成，构建中...")
subprocess.run("python scripts/build_web_data.py", shell=True)