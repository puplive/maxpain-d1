"""下载郑商所 广期所 中金所数据 存入 ../file/
郑商所下载失败
用法: python scripts/download_czce_cffex_gfex.py --year 2026 --month 08
"""
import os
import requests
import zipfile
import datetime
import argparse
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 配置部分 ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 脚本所在目录父级

# 各交易所配置
CONFIGS = [
    { # 中金所
        "name": "cffex",
        "base_url": "http://www.cffex.com.cn/sj/historysj/",
        "file_pattern": "{year}{month}/zip/{year}{month}.zip", # month需为两位数字
        "save_dir": os.path.join(PROJECT_ROOT, "file", "cffex"),
        "frequency": "monthly",
        "is_zip": True,
    },
    { # 郑商所-期权
        "name": "czce_option",
        "base_url": "https://www.czce.com.cn/cn/DFSStaticFiles/Option/",
        "file_pattern": "{year}/ALLOPTIONS{year}.zip",
        "save_dir": os.path.join(PROJECT_ROOT, "file", "czce"),
        "frequency": "yearly",
        "is_zip": True,
    },
    { # 郑商所-期货
        "name": "czce_future",
        "base_url": "https://www.czce.com.cn/cn/DFSStaticFiles/Future/",
        "file_pattern": "{year}/ALLFUTURES{year}.zip",
        "save_dir": os.path.join(PROJECT_ROOT, "file", "czce"),
        "frequency": "yearly",
        "is_zip": True,
    },
    { # 广期所-期货
        "name": "gfex_future",
        "base_url": "http://www.gfex.com.cn/gfex/gfexfile/history/",
        "file_pattern": "ALLFUTURES{year}.csv",
        "save_dir": os.path.join(PROJECT_ROOT, "file", "gfex"),
        "frequency": "yearly",
        "is_zip": False,
    },
    { # 广期所-期权
        "name": "gfex_option",
        "base_url": "http://www.gfex.com.cn/gfex/gfexfile/history/",
        "file_pattern": "ALLOPTIONS{year}.csv",
        "save_dir": os.path.join(PROJECT_ROOT, "file", "gfex"),
        "frequency": "yearly",
        "is_zip": False,
    },
]

def get_remote_url(config, year, month=None):
    """根据配置和日期生成完整的远程URL"""
    if config["frequency"] == "monthly":
        # 确保月份是两位数字，如 '08'
        url = config["base_url"] + config["file_pattern"].format(year=year, month=f"{month:02d}")
    else:  # yearly
        url = config["base_url"] + config["file_pattern"].format(year=year)
    return url

def download_file(url, save_path):
    """
    专门针对郑商所反爬策略优化的下载函数
    """
    try:
        print(f"正在下载: {url}")
        
        # 1. 伪造一个极其完整的浏览器请求头
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',  # 明确支持压缩
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
        }

        # 2. 创建会话，并针对郑商所添加关键的 Referer 头
        session = requests.Session()
        
        # 【核心修复】对于郑商所，手动设置 Referer 为它的主页
        if 'czce.com.cn' in url:
            # 这一步至关重要：告诉服务器，我们是从它的主页点击过来的
            session.headers.update({'Referer': 'https://www.czce.com.cn/'})
            print("  已为郑商所请求设置 Referer 头")
            
            # 先访问一下主页，获取可能需要的 Cookie
            try:
                print("  尝试访问主页以获取会话Cookie...")
                session.get('https://www.czce.com.cn', headers=headers, timeout=10, verify=False)
            except Exception as e:
                print(f"  访问主页获取Cookie时出现警告（可忽略）: {e}")

        # 3. 发送主要的下载请求
        response = session.get(
            url, 
            headers=headers, 
            stream=True, 
            timeout=30, 
            verify=False,  # 绕过SSL验证
            allow_redirects=True
        )
        
        # 打印服务器返回的HTTP状态码，便于调试
        print(f"  服务器响应状态码: {response.status_code}")
        
        # 如果还是 412，打印服务器返回的提示信息（如果有的话）
        if response.status_code == 412:
            print(f"  错误详情: {response.text[:200]}")
            response.raise_for_status()  # 触发异常

        response.raise_for_status()

        # 4. 检查文件大小
        content_length = response.headers.get('content-length')
        if content_length and int(content_length) == 0:
            print(f"警告: 服务器返回文件大小为0，可能数据尚未生成")
            return False

        # 5. 保存文件
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        file_size = os.path.getsize(save_path)
        print(f"文件已保存: {save_path} (大小: {file_size} 字节)")
        return True

    except requests.exceptions.HTTPError as e:
        print(f"下载失败，HTTP错误 {e.response.status_code}: {url}")
        return False
    except Exception as e:
        print(f"下载失败 {url}: {e}")
        return False
       
import subprocess
import os

def download_with_curl(url, save_path):
    """
    使用系统curl命令进行下载，模拟地址栏请求（专门针对郑商所）
    """
    print(f"正在通过 curl 命令下载: {url}")
    
    # 确保保存目录存在
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    
    # 你已验证成功的完整curl命令参数
    cmd = [
        'curl', '-L', '-O', '--compressed',
        '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        '-H', 'Accept-Language: zh-CN,zh;q=0.9',
        '--header', 'Referer: https://www.czce.com.cn/',
        '--output', save_path,  # 指定保存路径
        url
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        # 检查是否成功
        if result.returncode == 0 and os.path.exists(save_path) and os.path.getsize(save_path) > 0:
            print(f"curl 下载成功: {save_path} (大小: {os.path.getsize(save_path)} 字节)")
            return True
        else:
            print(f"curl 下载失败，返回码: {result.returncode}")
            if result.stderr:
                print(f"错误信息: {result.stderr}")
            # 如果文件存在但大小为0，删除它
            if os.path.exists(save_path) and os.path.getsize(save_path) == 0:
                os.remove(save_path)
            return False
            
    except subprocess.TimeoutExpired:
        print(f"curl 下载超时: {url}")
        return False
    except Exception as e:
        print(f"执行 curl 命令时发生错误: {e}")
        return False
    
def extract_zip(zip_path, extract_to):
    """解压ZIP文件，自动覆盖内部文件"""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
        print(f"解压成功: {zip_path} -> {extract_to}")
        # 如果想解压后删除zip包，取消下一行注释
        os.remove(zip_path) 
        return True
    except Exception as e:
        print(f"解压失败 {zip_path}: {e}")
        return False

def process_exchange(config, year, month=None):
    print(f"\n--- 开始处理: {config['name']} ---")
    
    url = get_remote_url(config, year, month)
    filename = os.path.basename(url)
    save_path = os.path.join(config["save_dir"], filename)
    
    # --- 关键判断：是否为郑商所 ---
    # if 'czce.com.cn' in url:
    #     # 郑商所：使用 curl 命令下载
    #     success = download_with_curl(url, save_path)
    # else:
    #     # 中金所、广期所：继续使用原来的 requests 方法
    #     success = download_file(url, save_path)  # 确保你的 download_file 函数存在
    
    # if not success:
    #     return
    
    # --- 解压逻辑（保持不变） ---
    if config["is_zip"] and save_path.endswith('.zip'):
        # if config["frequency"] == "monthly":
        #     extract_target = os.path.join(config["save_dir"], f"{year}{month:02d}")
        # else:
        dir_name = os.path.splitext(filename)[0]
        extract_target = os.path.join(config["save_dir"], dir_name)
        
        os.makedirs(extract_target, exist_ok=True)
        extract_zip(save_path, extract_target)

def main():
    # 1. 解析命令行参数
    parser = argparse.ArgumentParser(description='下载期货交易所数据')
    parser.add_argument('--year', type=int, help='年份，如 2026；不指定则使用当前系统年份')
    parser.add_argument('--month', type=int, help='月份（1-12），仅对中金所月更数据有效；不指定则使用当前系统月份')
    args = parser.parse_args()

    now = datetime.datetime.now()
    current_year = args.year if args.year is not None else now.year
    current_month = args.month if args.month is not None else now.month

    print(f"===== 开始下载截至 {current_year}年{current_month}月 的累积数据 =====")

    # 中金所
    process_exchange(CONFIGS[0], current_year, current_month)
    # 郑商所与广期所
    for config in CONFIGS[1:]:
        process_exchange(config, current_year)
    
    print("\n===== 所有任务执行完毕 =====")

if __name__ == "__main__":
    main()