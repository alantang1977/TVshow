#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import logging
import time
import argparse
import requests
import gzip
import xml.etree.ElementTree as ET
import re
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("iptv_update.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("IPTV-Main")

# GitHub环境检测
IS_GITHUB_ENV = os.environ.get('GITHUB_ACTIONS', 'false').lower() == 'true'

def create_session_with_retries():
    """创建带有重试机制的会话，特别优化GitHub环境"""
    session = requests.Session()
    
    # 定义重试策略，修改重试次数为2次
    retry_strategy = Retry(
        total=2,  # 重试次数改为2次
        backoff_factor=1,  # 指数退避因子
        status_forcelist=[403, 429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    
    # 设置基础请求头，模拟浏览器
    base_headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
        'Connection': 'keep-alive',
        'DNT': '1',  # 不跟踪请求
        'Upgrade-Insecure-Requests': '1'
    }
    
    # GitHub特定请求头优化
    if IS_GITHUB_ENV:
        base_headers['User-Agent'] = 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0'
        base_headers['Referer'] = 'https://github.com/'
    else:
        base_headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
    
    session.headers.update(base_headers)
    return session

# 准备多个用户代理，用于轮换
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Linux; Android 13; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36'
]

def load_config():
    """加载配置文件"""
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info(f"成功从 {config_path} 加载配置")
        return config
    except Exception as e:
        logger.error(f"加载配置文件失败: {str(e)}")
        sys.exit(1)

def download_source_with_retry(url, session=None, max_attempts=2):  # 重试次数改为2次
    """下载源文件并处理403等错误，支持多轮重试和用户代理轮换"""
    session = session or create_session_with_retries()
    attempt = 0
    
    while attempt < max_attempts:
        try:
            # 每轮尝试使用不同的用户代理
            session.headers['User-Agent'] = USER_AGENTS[attempt % len(USER_AGENTS)]
            
            # 对GitHub源添加特殊处理
            parsed_url = urlparse(url)
            if 'github.com' in parsed_url.netloc:
                # GitHub Raw内容需要特殊Accept头
                session.headers['Accept'] = 'application/vnd.github.raw+json, text/plain, */*'
                # 添加GitHub API友好的请求间隔
                if attempt > 0:
                    time.sleep(1.5)  # GitHub对频繁请求限制较严
            else:
                session.headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            
            response = session.get(url, timeout=30, stream=True)
            
            # 处理403响应的特殊情况
            if response.status_code == 403:
                logger.warning(f"尝试 {attempt+1}/{max_attempts} 访问 {url} 被拒绝，更换策略重试")
                
                # 对于GitHub，尝试添加Authorization头（如果有环境变量）
                if 'github.com' in parsed_url.netloc and os.environ.get('GITHUB_TOKEN'):
                    session.headers['Authorization'] = f'token {os.environ.get("GITHUB_TOKEN")}'
                else:
                    # 清除可能引起问题的头信息
                    session.headers.pop('Authorization', None)
                
                attempt += 1
                continue
                
            if response.status_code == 200:
                # 对于大文件，使用流式读取
                content = []
                for chunk in response.iter_content(chunk_size=8192):
                    content.append(chunk)
                return b''.join(content).decode('utf-8', errors='ignore')
                
            logger.error(f"下载源失败: {url}, 状态码: {response.status_code}")
            return None
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"尝试 {attempt+1}/{max_attempts} 下载 {url} 失败: {str(e)}, 重试中...")
            attempt += 1
            time.sleep(1 * attempt)  # 指数退避
        
    logger.error(f"超过最大重试次数，无法下载 {url}")
    return None

def parse_m3u_file(filepath):
    """解析M3U文件，提取频道信息和URL"""
    logger.info(f"解析M3U文件: {filepath}")
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        logger.error(f"读取文件失败: {filepath}, 错误: {str(e)}")
        return {}
    
    return parse_m3u_content(content, filepath)

def parse_m3u_content(content, source):
    """解析M3U内容字符串"""
    channels = {}
    
    lines = content.strip().split('\n')
    if not lines or not lines[0].startswith('#EXTM3U'):
        logger.warning(f"不是有效的M3U内容: {source}")
        return channels
    
    i = 1
    while i < len(lines):
        line = lines[i].strip()
        
        # 处理EXTINF行
        if line.startswith('#EXTINF'):
            extinf_line = line
            info = parse_extinf(extinf_line)
            
            # 获取频道ID
            channel_id = info.get('tvg-id') or info.get('tvg-name') or info.get('title')
            
            if not channel_id:
                i += 1
                continue
            
            # 查找URL行
            url = None
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line and not next_line.startswith('#'):
                    url = next_line
                    break
                j += 1
            
            if url:
                # 将频道添加到集合中
                if channel_id in channels:
                    channels[channel_id][1].append(url)
                else:
                    channels[channel_id] = [info, [url]]
                
                i = j + 1
            else:
                i += 1
        else:
            i += 1
    
    logger.info(f"从 {source} 解析出 {len(channels)} 个频道")
    return channels

def parse_extinf(extinf_line):
    """解析EXTINF行，提取频道信息"""
    info = {}
    
    try:
        # 提取时长和标题
        parts = extinf_line.split(',', 1)
        if len(parts) > 1:
            info['title'] = parts[1].strip()
        
        # 提取属性
        attrs_part = parts[0]
        pattern = r'(\w+[-\w]*)\s*=\s*"([^"]*)"'
        matches = re.findall(pattern, attrs_part)
        
        for key, value in matches:
            info[key] = value
            
    except Exception as e:
        logger.error(f"解析EXTINF失败: {str(e)}")
        
    return info

def download_and_parse_epg(config):
    """下载并解析EPG数据"""
    if "epg_urls" not in config or not config["epg_urls"]:
        logger.info("未配置EPG URL，跳过EPG处理")
        return {}
        
    logger.info("开始下载和解析EPG数据")
    session = create_session_with_retries()
    epg_data = {}  # 格式: {频道ID: {"id": id, "name": name, "icon": icon_url}}
    
    for epg_url in config["epg_urls"]:
        logger.info(f"下载EPG: {epg_url}")
        try:
            content = download_source_with_retry(epg_url, session)
            if not content:
                continue
                
            # 处理二进制内容（如果是gzip）
            if epg_url.endswith('.gz'):
                try:
                    # 尝试将文本内容转换为字节流进行解压
                    content = gzip.decompress(content.encode('utf-8', errors='ignore'))
                except Exception as e:
                    logger.error(f"解压EPG数据失败: {str(e)}")
                    continue
            else:
                content = content.encode('utf-8', errors='ignore')
                
            # 解析XML
            try:
                root = ET.fromstring(content)
                
                # 查找频道信息
                for channel in root.findall(".//channel"):
                    channel_id = channel.get('id')
                    if not channel_id:
                        continue
                        
                    # 获取频道名称
                    display_name = channel.find('.//display-name')
                    name = display_name.text if display_name is not None else ""
                    
                    # 获取频道图标
                    icon = channel.find('.//icon')
                    icon_url = icon.get('src') if icon is not None else ""
                    
                    epg_data[channel_id] = {
                        "id": channel_id,
                        "name": name,
                        "icon": icon_url
                    }
                    
            except ET.ParseError as e:
                logger.error(f"解析EPG XML失败: {str(e)}")
                continue
                
        except Exception as e:
            logger.error(f"处理EPG失败: {str(e)}")
            continue
            
    logger.info(f"解析完成，共获取 {len(epg_data)} 个EPG频道信息")
    return epg_data

def load_invalid_sources_cache():
    """加载无效源缓存"""
    cache_path = os.path.join(os.path.dirname(__file__), "data", "invalid_sources_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载无效源缓存失败: {str(e)}")
    return []

def save_invalid_sources_cache(invalid_sources):
    """保存无效源缓存"""
    cache_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, "invalid_sources_cache.json")
    
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(invalid_sources, f, ensure_ascii=False, indent=2)
        logger.info(f"已保存 {len(invalid_sources)} 个无效源到缓存")
    except Exception as e:
        logger.error(f"保存无效源缓存失败: {str(e)}")

def main():
    """主函数"""
    config = load_config()
    
    # 加载无效源缓存
    invalid_sources_cache = load_invalid_sources_cache()
    logger.info(f"已加载 {len(invalid_sources_cache)} 个无效源缓存")
    
    # 初始化收集器
    from collector import IPTVSourceCollector
    collector = IPTVSourceCollector(config)
    
    # 收集直播源
    source_files = collector.collect()
    if not source_files:
        logger.error("未收集到任何直播源文件，程序退出")
        return
    
    # 解析所有收集到的直播源
    all_channels = {}
    for file in source_files:
        channels = parse_m3u_file(file)
        # 合并频道，过滤掉缓存中的无效源
        for channel_id, (info, urls) in channels.items():
            # 过滤掉缓存中的无效源
            filtered_urls = [url for url in urls if url not in invalid_sources_cache]
            
            if not filtered_urls:
                continue
                
            if channel_id in all_channels:
                all_channels[channel_id][1].extend(filtered_urls)
            else:
                all_channels[channel_id] = [info, filtered_urls]
    
    if not all_channels:
        logger.error("未解析到任何有效频道，程序退出")
        return
    
    # 检查直播源有效性
    from checker import IPTVSourceChecker
    checker = IPTVSourceChecker(config)
    checked_results = checker.check(all_channels)
    
    # 更新无效源缓存
    new_invalid_sources = []
    for channel_id, result in checked_results.items():
        for url, is_valid, _ in result["sources"]:
            if not is_valid and url not in invalid_sources_cache:
                new_invalid_sources.append(url)
    
    # 合并并保存新的无效源缓存
    updated_invalid_sources = list(set(invalid_sources_cache + new_invalid_sources))
    save_invalid_sources_cache(updated_invalid_sources)
    
    # 下载并解析EPG数据
    epg_data = download_and_parse_epg(config)
    
    # 创建输出目录
    output_dir = os.path.join(os.path.dirname(__file__), config["output_dir"])
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, config["output_file"])
    
    # 生成最终的M3U文件
    generate_m3u_file(checked_results, epg_data, config, output_path)

def generate_m3u_file(checked_results, epg_data, config, output_path):
    """生成最终的M3U文件"""
    logger.info(f"开始生成M3U文件: {output_path}")
    
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            # 写入M3U头部
            f.write("#EXTM3U x-tvg-url=\"combined_epg.xml.gz\"\n")
            
            # 按类别整理频道
            categorized = {cat: [] for cat in config["categories"]}
            categorized["其他"] = []  # 确保"其他"类别存在
            
            for channel_id, result in checked_results.items():
                # 只保留有效源
                valid_sources = [(url, latency) for url, is_valid, latency in result["sources"] if is_valid]
                if not valid_sources:
                    continue
                
                # 按延迟排序，延迟低的优先
                valid_sources.sort(key=lambda x: x[1])
                
                info = result["info"]
                channel_name = info.get("title", channel_id)
                
                # 尝试标准化频道名称
                for pattern, standard_name in config["channel_name_map"].items():
                    if re.search(pattern, channel_name, re.IGNORECASE):
                        channel_name = standard_name
                        break
                
                # 确定频道类别
                category = "其他"
                for cat in config["categories"]:
                    if cat in channel_name:
                        category = cat
                        break
                
                categorized[category].append((channel_id, channel_name, info, valid_sources))
            
            # 写入每个类别的频道
            for category in config["categories"]:
                if not categorized[category]:
                    continue
                
                # 写入类别信息
                f.write(f"\n# 类别: {category}\n")
                
                # 写入该类别的所有频道
                for channel_id, channel_name, info, valid_sources in categorized[category]:
                    # 构建EXTINF行
                    extinf_attrs = []
                    if "tvg-id" in info:
                        extinf_attrs.append(f'tvg-id="{info["tvg-id"]}"')
                    else:
                        extinf_attrs.append(f'tvg-id="{channel_id}"')
                        
                    if "tvg-name" in info:
                        extinf_attrs.append(f'tvg-name="{info["tvg-name"]}"')
                    else:
                        extinf_attrs.append(f'tvg-name="{channel_name}"')
                        
                    if "tvg-logo" in info:
                        extinf_attrs.append(f'tvg-logo="{info["tvg-logo"]}"')
                    elif channel_id in epg_data and epg_data[channel_id]["icon"]:
                        extinf_attrs.append(f'tvg-logo="{epg_data[channel_id]["icon"]}"')
                        
                    extinf_line = f'#EXTINF:-1 {" ".join(extinf_attrs)},{channel_name}'
                    f.write(f"{extinf_line}\n")
                    
                    # 写入所有有效源，第一个为主源，其余为备用源
                    for url, _ in valid_sources:
                        f.write(f"{url}\n")
        
        logger.info(f"成功生成M3U文件: {output_path}，包含 {sum(len(v) for v in categorized.values())} 个有效频道")
        
    except Exception as e:
        logger.error(f"生成M3U文件失败: {str(e)}")

if __name__ == "__main__":
    main()
