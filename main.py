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
from concurrent.futures import ThreadPoolExecutor
from collector import IPTVSourceCollector
from checker import IPTVSourceChecker

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

def parse_m3u_file(filepath):
    """解析M3U文件，提取频道信息和URL"""
    logger.info(f"解析M3U文件: {filepath}")
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        logger.error(f"读取文件失败: {filepath}, 错误: {str(e)}")
        return {}
    
    channels = {}
    
    lines = content.strip().split('\n')
    if not lines or not lines[0].startswith('#EXTM3U'):
        logger.warning(f"不是有效的M3U文件: {filepath}")
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
    
    logger.info(f"从文件 {filepath} 解析出 {len(channels)} 个频道")
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
    
    epg_data = {}  # 格式: {频道ID: {"id": id, "name": name, "icon": icon_url}}
    
    for epg_url in config["epg_urls"]:
        logger.info(f"下载EPG: {epg_url}")
        try:
            response = requests.get(epg_url, timeout=120)
            if response.status_code != 200:
                logger.error(f"下载EPG失败，状态码: {response.status_code}")
                continue
                
            # 检查是否为gzip格式
            if epg_url.endswith('.gz'):
                try:
                    content = gzip.decompress(response.content)
                except Exception as e:
                    logger.error(f"解压EPG数据失败: {str(e)}")
                    continue
            else:
                content = response.content
                
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
                    
                    # 存储频道信息
                    if channel_id not in epg_data:
                        epg_data[channel_id] = {
                            "id": channel_id,
                            "name": name,
                            "icon": icon_url
                        }
                    elif not epg_data[channel_id]["icon"] and icon_url:
                        # 如果当前EPG数据没有图标但新数据有，则更新
                        epg_data[channel_id]["icon"] = icon_url
                        
                logger.info(f"从 {epg_url} 解析出 {len(root.findall('.//channel'))} 个频道信息")
                    
            except ET.ParseError as e:
                logger.error(f"解析EPG XML数据失败: {str(e)}")
                continue
                
        except Exception as e:
            logger.error(f"处理EPG出错: {str(e)}")
            continue
    
    logger.info(f"EPG数据解析完成，共收集 {len(epg_data)} 个频道信息")
    return epg_data

def match_channels_with_epg(sources_data, epg_data, config):
    """将频道与EPG数据匹配"""
    if not epg_data:
        return sources_data
        
    logger.info("开始匹配频道与EPG数据")
    
    # 简化频道名称的函数，用于匹配
    def simplify_name(name):
        if not name:
            return ""
        # 移除空格和特殊字符
        simplified = re.sub(r'[^\w\u4e00-\u9fff]', '', name.lower())
        # 常见频道名称替换
        replacements = {
            'cctv': 'cctv',
            'central': 'cctv',
            'china': 'cctv',
            'hong': 'hk',
            'tai': 'tw',
            'television': 'tv',
            'channel': ''
        }
        for old, new in replacements.items():
            simplified = simplified.replace(old, new)
        return simplified
    
    # 创建EPG索引，用于快速查找
    epg_index = {}
    for epg_id, data in epg_data.items():
        simple_name = simplify_name(data["name"])
        if simple_name:
            epg_index[simple_name] = epg_id
    
    # 匹配频道
    matched_count = 0
    for channel_id, data in sources_data.items():
        info = data["info"]
        title = info.get('title', '')
        
        # 规范化频道名称
        normalized_title = normalize_channel_name(title, config)
        if normalized_title != title:
            info['title'] = normalized_title
            
        # 尝试直接匹配EPG ID
        if channel_id in epg_data:
            # 更新tvg-id和tvg-logo
            info['tvg-id'] = epg_data[channel_id]["id"]
            if not info.get('tvg-logo') and epg_data[channel_id]["icon"]:
                info['tvg-logo'] = epg_data[channel_id]["icon"]
            matched_count += 1
            continue
            
        # 尝试通过简化名称匹配
        simple_title = simplify_name(normalized_title)
        if simple_title in epg_index:
            epg_id = epg_index[simple_title]
            # 更新tvg-id和tvg-logo
            info['tvg-id'] = epg_data[epg_id]["id"]
            if not info.get('tvg-logo') and epg_data[epg_id]["icon"]:
                info['tvg-logo'] = epg_data[epg_id]["icon"]
            matched_count += 1
            continue
            
        # 尝试模糊匹配
        for epg_name, epg_id in epg_index.items():
            if (epg_name in simple_title) or (simple_title in epg_name and len(simple_title) > 3):
                # 更新tvg-id和tvg-logo
                info['tvg-id'] = epg_data[epg_id]["id"]
                if not info.get('tvg-logo') and epg_data[epg_id]["icon"]:
                    info['tvg-logo'] = epg_data[epg_id]["icon"]
                matched_count += 1
                break
    
    logger.info(f"频道与EPG匹配完成，成功匹配 {matched_count} 个频道")
    return sources_data

def normalize_channel_name(name, config):
    """规范化频道名称"""
    if not name:
        return name
        
    name_lower = name.lower()
    
    # 尝试匹配映射表
    if "channel_name_map" in config:
        for pattern, normalized_name in config["channel_name_map"].items():
            if re.search(pattern, name_lower):
                return normalized_name
                
    return name

def should_exclude_channel(info, url, config):
    """检查是否应该排除某个频道或源"""
    # 检查URL是否包含被排除的源
    if "excluded_sources" in config:
        for excluded_source in config["excluded_sources"]:
            if excluded_source in url:
                return True
    
    # 检查频道ID是否为数字
    tvg_id = info.get('tvg-id', '')
    if tvg_id and tvg_id.isdigit() and len(tvg_id) > 6:
        return True
        
    # 检查频道名称是否包含敏感词
    if "excluded_keywords" in config:
        title = info.get('title', '').lower()
        for keyword in config["excluded_keywords"]:
            if keyword.lower() in title:
                return True
                
    return False

def categorize_channel(channel_title, config):
    """对频道进行分类"""
    categories = config.get("categories", [])
    title_lower = channel_title.lower()
    
    # 优先匹配央视
    if re.match(r'cctv-?\d+', title_lower):
        return "央视"
    
    # 匹配其他分类
    category_mappings = {
        "卫视": ["卫视", "东方", "江苏", "湖南", "浙江", "北京", "山东", "广东", "深圳"],
        "地方": ["地方", "市", "县", "区", "省"],
        "港澳台": ["香港", "澳门", "台湾", "翡翠", "明珠", "凤凰", "中天", "东森", "tvbs", "本港台"],
        "国际": ["国际", "bbc", "cnn", "nhk", "discovery", "hbo"],
        "体育": ["体育", "cctv5", "赛事", "足球", "篮球", "奥运"],
        "影视": ["电影", "电视剧", "影视", "剧场"],
        "纪录": ["纪录", "documentary"],
        "少儿": ["少儿", "卡通", "动画", "儿童"],
        "音乐": ["音乐", "mtv", "audio"],
        "新闻": ["新闻", "news"]
    }
    
    for category, keywords in category_mappings.items():
        if category in categories:
            for keyword in keywords:
                if keyword.lower() in title_lower:
                    return category
    
    # 默认分类
    return "其他" if "其他" in categories else categories[0]

def sort_channels_in_category(category, channels):
    """按规则对分类内的频道进行排序"""
    # 央视按数字排序
    if category == "央视":
        def cctv_sort_key(channel):
            title = channel["info"]["title"]
            match = re.search(r'cctv-?(\d+)', title.lower())
            if match:
                return (0, int(match.group(1)))  # CCTV数字频道优先
            return (1, title)  # 其他央视相关频道
        return sorted(channels, key=cctv_sort_key)
    
    # 其他分类按名称拼音排序
    try:
        import pinyin
        return sorted(channels, key=lambda x: pinyin.get(x["info"]["title"], format="strip", delimiter=""))
    except ImportError:
        # 没有pinyin库时按原文字排序
        return sorted(channels, key=lambda x: x["info"]["title"])

def generate_output_files(config, checked_data):
    """生成分类排序后的M3U和TXT文件"""
    output_dir = config["output_dir"]
    os.makedirs(output_dir, exist_ok=True)
    
    # 创建分类容器
    categories = config.get("categories", [])
    categorized = {cat: [] for cat in categories}
    
    # 过滤有效频道并分类
    for channel_id, channel_data in checked_data.items():
        # 筛选有效源并按延迟排序
        valid_sources = [src for src in channel_data["sources"] if src[1]]
        if not valid_sources:
            continue
            
        # 按延迟排序（升序）
        valid_sources.sort(key=lambda x: x[2])
        
        # 排除不需要的频道
        if should_exclude_channel(channel_data["info"], valid_sources[0][0], config):
            continue
            
        # 分类
        category = categorize_channel(channel_data["info"]["title"], config)
        if category not in categorized:
            categorized[category] = []
            
        categorized[category].append({
            "id": channel_id,
            "info": channel_data["info"],
            "sources": valid_sources
        })
    
    # 生成合并的M3U文件
    m3u_path = os.path.join(output_dir, config["output_file"])
    with open(m3u_path, 'w', encoding='utf-8') as m3u_file:
        m3u_file.write("#EXTM3U x-tvg-url=\"https://epg.51zmt.top:8000/e.xml.gz\"\n")
        
        # 按配置的分类顺序处理
        for category in categories:
            if not categorized[category]:
                continue
                
            # 分类内排序
            sorted_channels = sort_channels_in_category(category, categorized[category])
            
            # 写入分类标记（作为注释）
            m3u_file.write(f"\n# 分类: {category}\n")
            
            # 写入频道
            for channel in sorted_channels:
                info = channel["info"]
                extinf_parts = []
                extinf_parts.append(f"tvg-id=\"{info.get('tvg-id', '')}\"")
                if info.get('tvg-logo'):
                    extinf_parts.append(f"tvg-logo=\"{info['tvg-logo']}\"")
                extinf_line = f"#EXTINF:-1 {','.join(extinf_parts)},{info.get('title', '未知频道')}"
                
                # 写入主源
                m3u_file.write(f"{extinf_line}\n")
                m3u_file.write(f"{channel['sources'][0][0]}\n")
                
                # 写入备用源（带注释标记）
                for url, _, latency in channel['sources'][1:]:
                    m3u_file.write(f"# 备用源 (延迟: {latency:.2f}s): {url}\n")
    
    # 生成TXT文件（仅主源）
    txt_path = os.path.join(output_dir, config["output_file"].replace('.m3u', '.txt'))
    with open(txt_path, 'w', encoding='utf-8') as txt_file:
        for category in categories:
            if not categorized[category]:
                continue
                
            # 分类内排序
            sorted_channels = sort_channels_in_category(category, categorized[category])
            
            # 写入分类标题
            txt_file.write(f"\n【{category}】\n")
            
            # 写入频道
            for channel in sorted_channels:
                title = channel["info"].get('title', '未知频道')
                main_url = channel['sources'][0][0]
                txt_file.write(f"{title},{main_url}\n")
    
    logger.info(f"已生成M3U文件: {m3u_path}")
    logger.info(f"已生成TXT文件: {txt_path}")

def main():
    """主函数"""
    config = load_config()
    
    # 1. 收集直播源
    collector = IPTVSourceCollector(config)
    collected_files = collector.collect()
    if not collected_files:
        logger.error("没有收集到任何直播源文件，程序退出")
        return
    
    # 2. 解析所有收集到的文件
    all_channels = {}
    for file in collected_files:
        channels = parse_m3u_file(file)
        # 合并频道
        for channel_id, (info, urls) in channels.items():
            if channel_id in all_channels:
                all_channels[channel_id][1].extend(urls)
            else:
                all_channels[channel_id] = [info, urls]
    
    logger.info(f"所有文件解析完成，共 {len(all_channels)} 个独特频道")
    
    # 3. 检测直播源有效性
    checker = IPTVSourceChecker(config)
    checked_data = checker.check(all_channels)
    
    # 4. 下载并匹配EPG数据
    epg_data = download_and_parse_epg(config)
    checked_data = match_channels_with_epg(checked_data, epg_data, config)
    
    # 5. 生成输出文件
    generate_output_files(config, checked_data)
    
    logger.info("所有操作完成")

if __name__ == "__main__":
    main()
