# -*- coding: utf-8 -*-
import os
import sys
import json
import re
import requests
from pathlib import Path

# ================== 从环境变量读取配置 ==================
APP_ID = os.environ.get("FEISHU_APP_ID")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
APP_TOKEN = os.environ.get("FEISHU_APP_TOKEN")
TABLE_ID = os.environ.get("FEISHU_TABLE_ID")

if not all([APP_ID, APP_SECRET, APP_TOKEN, TABLE_ID]):
    raise Exception("缺少必要的环境变量，请检查 GitHub Secrets 配置")

# ================== 路径与状态设置 ==================
DATA_DIR = Path("资料文件夹")
DATA_DIR.mkdir(exist_ok=True)
MANIFEST_PATH = Path("manifest.json")
LAST_RUN_FILE = Path("last_run_time.txt")

FULL_SYNC = "--full-sync" in sys.argv

# ================== 飞书 API ==================
def get_tenant_access_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取 token 失败: {data}")
    return data["tenant_access_token"]

def get_all_records(token):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}"}
    all_records = []
    page_token = None
    page_num = 1

    print(f"🌐 请求飞书 API URL: {url}")
    
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
            
        print(f"📄 正在获取第 {page_num} 页数据...")
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        
        data = resp.json()
        print(f"🔍 飞书原始返回（第 {page_num} 页）: {json.dumps(data, ensure_ascii=False)}")

        if data.get("code") != 0:
            print(f"❌ 飞书 API 报错，错误码: {data.get('code')}，信息: {data}")
            break

        records = data.get("data", {}).get("items")
        if records is None:
            print("⚠️ API 返回中未找到 'items' 字段，请检查 APP_TOKEN 或 TABLE_ID 是否正确！")
            break

        print(f"📊 本页获取到 {len(records)} 条记录")
        all_records.extend(records)

        page_token = data.get("data", {}).get("page_token")
        if not page_token:
            print("✅ 所有分页读取完毕")
            break
        page_num += 1

    return all_records

def download_file(url, save_path, token):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, stream=True, timeout=30)
        if resp.status_code == 200:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        else:
            print(f"   下载失败: HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"   下载异常: {e}")
        return False

def sync_from_feishu():
    print("🔄 开始同步飞书数据...")
    if FULL_SYNC:
        print("▶️ 检测到 --full-sync 参数，强制全量同步并覆盖")
    else:
        if LAST_RUN_FILE.exists():
            print(f"⏱️ 增量模式，上次同步时间为: {LAST_RUN_FILE.read_text().strip()}")
        else:
            print("⏱️ 首次运行，触发全量同步")
    
    token = get_tenant_access_token()
    records = get_all_records(token)
    
    if not records:
        print("⚠️ 没有获取到任何记录。")
        print("💡 排查提示：如果飞书返回的是空列表 []，请务必去飞书表格 -> 右上角 ... -> 权限管理 -> 添加本应用的 APP_ID 作为协作者！")
        return
    print(f"📦 总获取到 {len(records)} 条记录")

    synced_count = 0
    for record in records:
        fields = record.get("fields", {})
        name = fields.get("姓名")
        if not name:
            print(f"⚠️ 跳过未命名的记录 (无姓名) - ID: {record.get('record_id')}")
            continue

        person_dir = DATA_DIR / name
        person_dir.mkdir(exist_ok=True)
        has_new_file = False
        
        # ====== 文本字段处理：首行作为标题/文件名 ======
        text_content = None
        possible_keys = ["文本", "文字资料", "文本资料"]
        for key in possible_keys:
            if fields.get(key) and isinstance(fields.get(key), str):
                text_content = fields.get(key)
                break
        
        if text_content:
            # 提取首行作为标题/文件名
            lines = text_content.split('\n')
            first_line = lines[0].strip() if lines else "无标题"
            
            # 清理文件名中的非法字符（Windows/Unix 不兼容字符）
            safe_filename = re.sub(r'[<>:"/\\|?*]', '_', first_line)
            safe_filename = safe_filename.strip() or "无标题"
            
            # 限制文件名长度，避免过长
            if len(safe_filename) > 100:
                safe_filename = safe_filename[:100]
            
            txt_filename = f"{safe_filename}.txt"
            save_path = person_dir / txt_filename
            
            # 检查文件是否已存在（按新文件名检查）
            if not save_path.exists() or FULL_SYNC:
                print(f"📄 保存文本资料: {name}/{txt_filename}")
                
                # 正文 = 除首行外的内容
                body = '\n'.join(lines[1:]).strip()
                formatted_text = f"========== {first_line} ==========\n\n{body}"
                
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(formatted_text)
                
                has_new_file = True
            else:
                print(f"   ⏭️ 文件已存在，跳过: {name}/{txt_filename}")
        else:
            print(f"   ℹ️ {name} 无文本内容")

        # 处理【图片音视频】附件
        media_field = fields.get("图片音视频")
        if media_field and isinstance(media_field, list):
            for media in media_field:
                filename = media.get("name", "media.jpg").replace('\n', '').replace('\r', '').replace('\t', '').strip()
                download_url = media.get("url")
                if not download_url:
                    print(f"   ⚠️ 媒体文件无 URL: {filename}")
                    continue
                save_path = person_dir / filename
                if save_path.exists() and not FULL_SYNC:
                    continue
                if download_file(download_url, save_path, token):
                    print(f"✅ 图片/视频: {name}/{filename}")
                    has_new_file = True
                else:
                    print(f"❌ 媒体下载失败: {name}/{filename}")

        if has_new_file:
            synced_count += 1

    # 生成 manifest.json
    print("📋 正在生成 manifest.json ...")
    manifest = {}
    for person_dir in DATA_DIR.iterdir():
        if person_dir.is_dir():
            files = [f.name for f in person_dir.iterdir() if f.is_file()]
            if files:
                manifest[person_dir.name] = files
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    
    import time
    LAST_RUN_FILE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    
    print(f"✅ manifest.json 已生成，共 {len(manifest)} 人")
    print(f"🎉 同步完成：{synced_count} 人新增或更新了资料")

if __name__ == "__main__":
    sync_from_feishu()
