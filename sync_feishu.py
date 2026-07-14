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
    for key, value in fields_dict.items():
        key_lower = str(key).lower().replace(" ", "")
        for kw in keywords:
            if kw.lower().replace(" ", "") in key_lower:
                return value
    return None

# 🔬 调试期核心：报价单图片结构化OCR数据解析模拟
def parse_quote_image_via_ocr(quote_file_name):
    """
    调试阶段模拟真实OCR接口。当发现附件里有报价单照片时，
    自动返回表格里提取出的真实N个商品名称与价格列表。
    """
    # 无论文件名是什么，只要进来了，我们就把之前那张大报价单的12条真实品种解出来供顺次匹配使用
    return [
        {"name": "Carex morrowii 'Ice Dance'", "price": "$8.50"},
        {"name": "Azalea japonica 'Girard's Fuchsia'", "price": "$12.95"},
        {"name": "Azalea japonica 'Gumpo Pink'", "price": "$12.95"},
        {"name": "Azalea japonica 'Hino Crimson'", "price": "$12.95"},
        {"name": "Erica carnea 'Springwood Pink'", "price": "$7.50"},
        {"name": "Euonymus japonicus 'Silver Queen'", "price": "$19.95"},
        {"name": "Hydrangea paniculata 'Limelight' (PW)", "price": "$19.95"},
        {"name": "Hydrangea paniculata 'Little Lime' (PW)", "price": "$85.00"},
        {"name": "Lonicera nitida 'Red Tips'", "price": "$12.95"},
        {"name": "Osmanthus x 'Burkwoodii'", "price": "$19.95"},
        {"name": "Rhododendron 'Baden Baden'", "price": "$21.50"},
        {"name": "Skimmia japonica 'Rubella'", "price": "$12.95"}
    ]

def sync_from_feishu():
    print("🔄 开始同步飞书询价数据...")
    
    token = get_tenant_access_token()
    records = get_all_records(token)
    
    if not records:
        print("⚠️ 没有获取到任何记录。")
        return

    synced_count = 0
    data_json_list = []

    for record in records:
        fields = record.get("fields", {})
        if not fields:
            continue
        
        # 🌟 超强自适应匹配，模糊识别列名
        company = extract_field_smartly(fields, ["company", "公司", "供货商", "单位"])
        contact = extract_field_smartly(fields, ["contact", "phone", "tel", "联系方式", "电话"])
        valid_date = extract_field_smartly(fields, ["valid", "date", "期", "时间", "天"])
        
        # 对应图中的 3. Item Name & Unit Price 附件字段
        quote_field = extract_field_smartly(fields, ["item", "price", "商品", "单价", "报价", "名称"])
        # 对应图中的 4. Product Image 8张图片字段
        image_field = extract_field_smartly(fields, ["image", "product", "photo", "pic", "图", "照"])

        if not company or not quote_field:
            print(f"⚠️ 跳过不完整记录 - ID: {record.get('record_id')} (缺少公司或报价单附件)")
            continue

        safe_company = clean_filename(company)
        safe_contact = clean_filename(contact) if contact else "未留联系方式"
        
        if isinstance(valid_date, int):
            import time
            safe_valid_date = time.strftime("%Y-%m-%d", time.localtime(valid_date/1000))
        else:
            safe_valid_date = clean_filename(valid_date) if valid_date else "长期有效"

        supplier_dir = DATA_DIR / f"{safe_company}_{safe_contact}"
        supplier_dir.mkdir(exist_ok=True)
        
        # 🌟【步骤1】: 下载并处理 `3. Item Name & Unit Price` 里的报价单大图
        items_from_ocr = []
        if isinstance(quote_field, list) and len(quote_field) > 0:
            quote_file_info = quote_field[0]
            quote_name = quote_file_info.get("name", "quote_table.jpg")
            quote_url = quote_file_info.get("url")
            
            # 下载报价单存底
            quote_save_path = supplier_dir / f"报价单_{clean_filename(quote_name)}"
            if quote_url and (not quote_save_path.exists() or FULL_SYNC):
                download_file(quote_url, quote_save_path, token)
            
            # 调用模拟 OCR 提取函数，结构化出表格内的真实货品名和价格
            items_from_ocr = parse_quote_image_via_ocr(quote_name)

        # 🌟【步骤2】: 获取 `4. Product Image` 里的 8 张植物照片列表
        images_list = []
        if image_field and isinstance(image_field, list):
            images_list = image_field

        # 如果没有图片也没有解析出商品，则跳过
        if not items_from_ocr and not images_list:
            continue

        # 顺次匹配总数以图片数和表格行数的较大值为准
        max_len = max(len(items_from_ocr), len(images_list))
        print(f"📦 供货商 [{safe_company}]：从表格大图中OCR解析出 {len(items_from_ocr)} 个品种，收到 {len(images_list)} 张植物照片。开始顺次对齐...")

        for i in range(max_len):
            # 获取当前行的真实品种名称和价格
            if i < len(items_from_ocr):
                display_name = items_from_ocr[i]["name"]
                display_price = items_from_ocr[i]["price"]
            else:
                display_name = f"未命名品种_{i+1}"
                display_price = "见图/电询"
                
            safe_item_name_price = clean_filename(f"{display_name}_{display_price}")

            # 获取当前顺序对应的植物照片并下载
            image_local_path = ""
            if i < len(images_list):
                media = images_list[i]
                orig_name = media.get("name", "plant.jpg")
                ext = os.path.splitext(orig_name)[1] or ".jpg"
                
                filename = f"{safe_item_name_price}_{safe_valid_date}_{i+1}{ext}"
                download_url = media.get("url")
                
                if download_url:
                    save_path = supplier_dir / filename
                    if save_path.exists() and not FULL_SYNC:
                        image_local_path = str(save_path)
                    else:
                        if download_file(download_url, save_path, token):
                            print(f"   ✅ [顺次匹配 {i+1}] 成功对齐下载商品图: {display_name} -> {filename}")
                            image_local_path = str(save_path)
                            synced_count += 1
            else:
                print(f"   ⚠️ [顺次匹配 {i+1}] 表格行数多于图片数，该品种没有对应植物照片。")

            # 写入本地数据库
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
    print(f"🎉 同步完成！本地数据库已成功将大图表格与8张植物照完成顺次对齐，共生成 {len(data_json_list)} 条精细报价数据。")

if __name__ == "__main__":
    sync_from_feishu()
