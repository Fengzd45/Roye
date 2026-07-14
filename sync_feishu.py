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
DATA_JSON_PATH = Path("data.json")
LAST_RUN_FILE = Path("last_run_time.txt")

FULL_SYNC = "--full-sync" in sys.argv

# 安全文件名清洗（杜绝特殊字符干扰路径）
def clean_filename(name):
    return re.sub(r'[\\/:*?"<>|]', "", str(name)).strip()

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
    print("🔄 开始同步飞书询价数据...")
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
        return
    print(f"📦 总获取到 {len(records)} 条记录")

    synced_count = 0
    
    # 临时收集用于生成 data.json 的结构化数据
    data_json_list = []

    for record in records:
        fields = record.get("fields", {})
        
        # 精准匹配飞书字段（兼容空格和下划线）
        company = fields.get("Quoting Company") or fields.get("Quoting_Company")
        contact = fields.get("Contact Information") or fields.get("Contact_Information")
        item_name_price = fields.get("Item Name & Unit Price") or fields.get("Item_Name_&_Unit_Price")
        valid_date = fields.get("Quotation Validity Date") or fields.get("Quotation_Validity_Date")
        image_field = fields.get("Product Image") or fields.get("Product_Image")

        if not company or not item_name_price:
            print(f"⚠️ 跳过不完整记录 - ID: {record.get('record_id')}")
            continue

        safe_company = clean_filename(company)
        safe_contact = clean_filename(contact) if contact else "未留联系方式"
        safe_item_name_price = clean_filename(item_name_price)
        safe_valid_date = clean_filename(valid_date) if valid_date else "长期有效"

        # 方案 B：创建 [供货商_联系方式] 一级子文件夹
        supplier_dir = DATA_DIR / f"{safe_company}_{safe_contact}"
        supplier_dir.mkdir(exist_ok=True)
        
        image_local_path = ""
        has_new_file = False

        # 处理产品图片下载
        if image_field and isinstance(image_field, list) and len(image_field) > 0:
            media = image_field[0]
            orig_name = media.get("name", "image.jpg")
            ext = os.path.splitext(orig_name)[1] or ".jpg"
            
            # 商品图片命名规则：商品名称与单价_有效期.jpg
            filename = f"{safe_item_name_price}_{safe_valid_date}{ext}"
            download_url = media.get("url")
            
            if download_url:
                save_path = supplier_dir / filename
                # 如果文件已存在且非全量同步，则跳过
                if save_path.exists() and not FULL_SYNC:
                    image_local_path = str(save_path)
                else:
                    if download_file(download_url, save_path, token):
                        print(f"✅ 下载成功: {supplier_dir.name}/{filename}")
                        image_local_path = str(save_path)
                        has_new_file = True
                    else:
                        print(f"❌ 下载失败: {supplier_dir.name}/{filename}")

        if has_new_file:
            synced_count += 1

        # 写入 JSON 数据库，解析单价与品名
        name_parts = safe_item_name_price.split('_')
        display_name = name_parts[0]
        display_price = name_parts[1] if len(name_parts) > 1 else "电询/未提供"

        data_json_list.append({
            "company": company,
            "contact": contact or "无",
            "item_name": display_name,
            "price": display_price,
            "valid_date": valid_date or "长期有效",
            "image_path": image_local_path.replace("\\", "/") if image_local_path else ""
        })

    # 生成传统的 manifest.json (与图库文件结构一致)
    print("📋 正在生成 manifest.json ...")
    manifest = {}
    for sub_dir in DATA_DIR.iterdir():
        if sub_dir.is_dir():
            files = [f.name for f in sub_dir.iterdir() if f.is_file()]
            if files:
                manifest[sub_dir.name] = files
                
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 生成前端卡片专用 data.json
    print("💾 正在生成 data.json ...")
    with open(DATA_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data_json_list, f, ensure_ascii=False, indent=2)
    
    import time
    LAST_RUN_FILE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    
    print(f"🎉 同步完成：{synced_count} 个供货商新增或更新了商品")

if __name__ == "__main__":
    sync_from_feishu()
