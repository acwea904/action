#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import hashlib
import base64

import cloudscraper
from bs4 import BeautifulSoup
import cv2
import requests

# ==== 配置 ====
BRIGHTNESS_THRESHOLD = 130
BATCH_SIZE = 100
TEMP_DIR = "temp_download"

# 起始ID（首次运行时使用）
START_ID = 342

# 目标私有仓库
TARGET_REPO = os.environ.get("TARGET_REPO", "")
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
TARGET_BRANCH = "main"

# 目标仓库中的路径
IMAGES_DIR = "ri"
FOLDERS = ["vd", "vl", "hd", "hl"]

scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
)


# ============ GitHub API ============

def github_get_sha(path: str) -> str | None:
    """只获取文件的SHA（用于二进制文件检查）"""
    if not GITHUB_TOKEN or not TARGET_REPO:
        return None
    
    url = f"https://api.github.com/repos/{TARGET_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("sha")
    except:
        pass
    return None


def github_get_json(path: str) -> tuple:
    """获取JSON文件内容和SHA"""
    if not GITHUB_TOKEN or not TARGET_REPO:
        return None, None
    
    url = f"https://api.github.com/repos/{TARGET_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data["sha"]
    except Exception as e:
        print(f"⚠️ 获取JSON失败 {path}: {e}")
    return None, None


def github_upload(path: str, content: bytes, message: str) -> bool:
    """上传文件到目标仓库"""
    if not GITHUB_TOKEN or not TARGET_REPO:
        print("❌ 缺少 GITHUB_TOKEN 或 TARGET_REPO")
        return False
    
    url = f"https://api.github.com/repos/{TARGET_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # 获取现有文件的SHA（用于更新）
    sha = github_get_sha(path)
    
    data = {
        "message": message,
        "content": base64.b64encode(content).decode("utf-8"),
        "branch": TARGET_BRANCH
    }
    if sha:
        data["sha"] = sha
    
    try:
        resp = requests.put(url, headers=headers, json=data, timeout=60)
        return resp.status_code in [200, 201]
    except Exception as e:
        print(f"❌ 上传失败 {path}: {e}")
        return False


def get_remote_json(path: str, default=None) -> dict:
    """从目标仓库获取JSON文件"""
    content, _ = github_get_json(path)
    if content:
        try:
            return json.loads(content)
        except:
            pass
    return default if default is not None else {}


def save_remote_json(path: str, data: dict, msg: str) -> bool:
    """保存JSON到目标仓库"""
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return github_upload(path, content, msg)


# ============ URL 处理 ============

def build_url(page_id: int) -> str:
    """根据ID构建完整URL"""
    return f"https://img.hyun.cc/index.php/archives/{page_id}.html"


# ============ 工具函数 ============

def get_file_hash(filepath: str) -> str:
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


# ============ 图片处理 ============

def scrape_images(url: str) -> list:
    """爬取页面中的图片链接"""
    print(f"🌐 爬取: {url}")
    
    try:
        resp = scraper.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = 'utf-8'
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return []
    
    soup = BeautifulSoup(resp.text, "lxml")
    images = []
    
    for idx, link in enumerate(soup.find_all("a", {"data-fancybox": True}), 1):
        href = link.get("href", "")
        if href.startswith("http"):
            images.append({"url": href, "index": idx})
    
    print(f"📷 找到 {len(images)} 张图片")
    return images


def download_image(url: str, save_path: str) -> bool:
    try:
        resp = scraper.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return False


def convert_to_webp(input_path: str, output_path: str) -> bool:
    try:
        img = cv2.imread(input_path)
        if img is None:
            return False
        cv2.imwrite(output_path, img, [cv2.IMWRITE_WEBP_QUALITY, 85])
        return True
    except:
        return False


def analyze_image(path: str) -> dict | None:
    """分析图片，返回分类文件夹"""
    try:
        img = cv2.imread(path)
        if img is None:
            return None
        
        h, w = img.shape[:2]
        if w < 10 or h < 10:
            return None
        
        orientation = "h" if w >= h else "v"
        
        resized = cv2.resize(img, (100, 100))
        lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)
        avg_l = lab[:, :, 0].mean()
        brightness = "d" if avg_l < BRIGHTNESS_THRESHOLD else "l"
        
        folder = orientation + brightness
        print(f"  📐 {w}x{h} L={avg_l:.1f} → {folder}")
        
        return {"folder": folder}
    except Exception as e:
        print(f"❌ 分析失败: {e}")
        return None


# ============ 页面处理 ============

def process_page(page_id: int) -> str:
    """
    处理单个页面
    返回: "success" | "empty" | "error"
    """
    url = build_url(page_id)
    
    print(f"\n{'='*50}")
    print(f"📂 处理页面 ID: {page_id}")
    print(f"🔗 {url}")
    print(f"{'='*50}\n")
    
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    # 爬取图片
    images = scrape_images(url)
    if not images:
        return "empty"
    
    # 获取远程数据
    hash_registry = get_remote_json(f"{IMAGES_DIR}/hash_registry.json", {})
    folder_counts = get_remote_json(f"{IMAGES_DIR}/count.json", {})
    
    for f in FOLDERS:
        if f not in folder_counts:
            folder_counts[f] = 0
    
    new_count = 0
    
    for img in images[:BATCH_SIZE]:
        idx = img["index"]
        temp_path = os.path.join(TEMP_DIR, f"temp_{idx}")
        webp_path = os.path.join(TEMP_DIR, f"temp_{idx}.webp")
        
        print(f"\n📥 [{idx}/{len(images)}] 下载中...")
        
        if not download_image(img["url"], temp_path):
            continue
        
        # 检查重复
        file_hash = get_file_hash(temp_path)
        if file_hash in hash_registry:
            print(f"  ⏭️ 跳过重复")
            os.remove(temp_path)
            continue
        
        # 分析图片
        info = analyze_image(temp_path)
        if not info:
            os.remove(temp_path)
            continue
        
        # 转换格式
        if not convert_to_webp(temp_path, webp_path):
            os.remove(temp_path)
            continue
        os.remove(temp_path)
        
        # 确定目标路径
        target_folder = info["folder"]
        folder_counts[target_folder] += 1
        new_num = folder_counts[target_folder]
        remote_path = f"{IMAGES_DIR}/{target_folder}/{new_num}.webp"
        
        # 上传图片到目标仓库
        with open(webp_path, "rb") as f:
            webp_data = f.read()
        
        if github_upload(remote_path, webp_data, f"Add {target_folder}/{new_num}.webp"):
            hash_registry[file_hash] = f"{target_folder}/{new_num}.webp"
            new_count += 1
            print(f"  ✅ 上传: {remote_path}")
        else:
            folder_counts[target_folder] -= 1
            print(f"  ❌ 上传失败")
        
        os.remove(webp_path)
    
    # 保存元数据到目标仓库
    if new_count > 0:
        save_remote_json(
            f"{IMAGES_DIR}/hash_registry.json", 
            hash_registry, 
            f"Update hash_registry (page {page_id})"
        )
        save_remote_json(
            f"{IMAGES_DIR}/count.json", 
            folder_counts, 
            f"Update count (page {page_id})"
        )
        print(f"\n💾 已更新 count.json 和 hash_registry.json")
    
    # 清理临时目录
    if os.path.exists(TEMP_DIR):
        for f in os.listdir(TEMP_DIR):
            os.remove(os.path.join(TEMP_DIR, f))
        os.rmdir(TEMP_DIR)
    
    print(f"\n✅ 页面 {page_id} 完成，新增 {new_count} 张")
    return "success"


# ============ 主函数 ============

def main():
    print("🚀 开始运行\n")
    
    # 检查配置
    if not GITHUB_TOKEN:
        print("❌ 缺少 GH_TOKEN 环境变量")
        return
    if not TARGET_REPO:
        print("❌ 缺少 TARGET_REPO 环境变量")
        return
    
    print(f"📦 目标仓库: {TARGET_REPO}")
    print(f"📁 存储目录: /{IMAGES_DIR}/\n")
    
    # 从目标仓库读取进度
    progress = get_remote_json("progress.json", {
        "completed": [],
        "last_success_id": START_ID - 1
    })
    
    # 确保字段存在
    if "completed" not in progress:
        progress["completed"] = []
    if "last_success_id" not in progress:
        progress["last_success_id"] = START_ID - 1
    
    completed_set = set(progress["completed"])
    current_id = progress["last_success_id"] + 1
    
    print(f"📊 已完成: {len(progress['completed'])} 个页面")
    print(f"📍 上次成功ID: {progress['last_success_id']}")
    print(f"📍 本次从 ID {current_id} 开始\n")
    
    # 循环处理
    while True:
        current_url = build_url(current_id)
        
        # 检查是否已完成
        if current_url in completed_set:
            print(f"⏭️ ID {current_id} 已完成，跳过")
            current_id += 1
            continue
        
        result = process_page(current_id)
        
        if result == "success":
            # ✅ 成功，更新进度
            progress["completed"].append(current_url)
            progress["last_success_id"] = current_id
            
            save_remote_json(
                "progress.json", 
                progress, 
                f"Complete: {current_url}"
            )
            print(f"💾 进度已保存: ID {current_id}\n")
            
            current_id += 1
            
        elif result == "empty":
            # ⏹️ 没有图片，停止执行
            print(f"\n⏹️ 页面 {current_id} 没有图片，停止执行")
            print(f"💡 下次运行将继续尝试: {current_url}")
            break
            
        else:
            # ❌ 出错，停止
            print(f"\n❌ 处理出错，停止")
            break
    
    print("\n🏁 运行结束")


if __name__ == "__main__":
    main()
