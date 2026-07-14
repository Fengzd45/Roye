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
            print(f"❌ 飞书 API 报错: {data}")
            break

        records = data.get("data", {}).get("items")
        if records is None:
            break

        print(f"📊 本页获取到 {len(records)} 条记录")
        all_records.extend(records)

        page_token = data.get("data", {}).get("page_token")
        if not page_token:
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
        return False
    except Exception as e:
        print(f"   下载异常: {e}")
        return False

# ✨ 核心：智能模糊提取器
def extract_field_smartly(fields_dict, keywords):
    """
    在飞书返回的字段字典中，只要键名(Key)包含 keywords 列表中的任何一个词（忽略大小写和空格），
    就将其值返回，实现对字段名变动的完美自适应！
    """
    for key, value in fields_dict.items():
        key_lower = str(key).lower().replace(" ", "")
        for kw in keywords:
            if kw.lower().replace(" ", "") in key_lower:
                return value
    return None

def sync_from_feishu():
    print("🔄 开始同步飞书询价数据...")
    
    token = get_tenant_access_token()
    records = get_all_records(token)
    
    if not records:
        print("⚠️ 没有获取到任何记录。")
        return

    # 🔬【关键调试诊断】：打印一条记录的所有实际字段名，供排查
    for record in records:
        fields = record.get("fields", {})
        if fields:
            print("🔬 [诊断信息] 飞书返回的实际字段名列表如下：")
            for k, v in fields.items():
                print(f"   - 字段名: '{k}', 对应值类型: {type(v).__name__}")
            break # 只打印一条即可

    synced_count = 0
    data_json_list = []

    for record in records:
        fields = record.get("fields", {})
        if not fields:
            continue
        
        # 🌟 超强自适应匹配，模糊识别列名
        company = extract_field_smartly(fields, ["company", "公司", "供货商", "单位"])
        contact = extract_field_smartly(fields, ["contact", "phone", "tel", "联系方式", "电话"])
        item_name_price = extract_field_smartly(fields, ["item", "price", "商品", "单价", "报价", "名称"])
        valid_date = extract_field_smartly(fields, ["valid", "date", "期", "时间", "天"])
        image_field = extract_field_smartly(fields, ["image", "product", "photo", "pic", "图", "照"])

        # 校验关键字段
        if not company or not item_name_price:
            print(f"⚠️ 跳过不完整记录 - ID: {record.get('record_id')} (由于未能匹配到公司名或商品信息)")
            continue

        safe_company = clean_filename(company)
        safe_contact = clean_filename(contact) if contact else "未留联系方式"
        safe_item_name_price = clean_filename(item_name_price)
        
        # 处理可能包含多种格式的有效期（如时间戳）
        if isinstance(valid_date, int):
            import time
            safe_valid_date = time.strftime("%Y-%m-%d", time.localtime(valid_date/1000))
        else:
            safe_valid_date = clean_filename(valid_date) if valid_date else "长期有效"

        supplier_dir = DATA_DIR / f"{safe_company}_{safe_contact}"
        supplier_dir.mkdir(exist_ok=True)
        
        image_local_path = ""
        has_new_file = False
        filename = ""

        if image_field and isinstance(image_field, list) and len(image_field) > 0:
            media = image_field[0]
            orig_name = media.get("name", "image.jpg")
            ext = os.path.splitext(orig_name)[1] or ".jpg"
            
            filename = f"{safe_item_name_price}_{safe_valid_date}{ext}"
            download_url = media.get("url")
            
            if download_url:
                save_path = supplier_dir / filename
                if save_path.exists() and not FULL_SYNC:
                    image_local_path = str(save_path)
                else:
                    if download_file(download_url, save_path, token):
                        print(f"✅ 成功下载商品图: {supplier_dir.name}/{filename}")
                        image_local_path = str(save_path)
                        has_new_file = True

        if filename and (has_new_file or not (supplier_dir / filename).exists()):
            synced_count += 1

        # 拆分品名与单价
        display_name = str(item_name_price)
        display_price = "见图/电询"
        
        for separator in ['_', '，', ',', ' ']:
            if separator in str(item_name_price):
                parts = str(item_name_price).split(separator)
                display_name = parts[0].strip()
                display_price = parts[1].strip()
                break

        data_json_list.append({
            "company": str(company).strip(),
            "contact": str(contact).strip() if contact else "无",
            "item_name": display_name,
            "price": display_price,
            "valid_date": safe_valid_date,
            "image_path": image_local_path.replace("\\", "/") if image_local_path else ""
        })

    # 生成传统的 manifest.json
    manifest = {}
    for sub_dir in DATA_DIR.iterdir():
        if sub_dir.is_dir():
            files = [f.name for f in sub_dir.iterdir() if f.is_file()]
            if files:
                manifest[sub_dir.name] = files
                
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 生成前端需要的最终 data.json
    with open(DATA_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data_json_list, f, ensure_ascii=False, indent=2)
    
    import time
    LAST_RUN_FILE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    print(f"🎉 同步完成！当前本地数据库共有 {len(data_json_list)} 条有效数据。")

if __name__ == "__main__":
    sync_from_feishu()
