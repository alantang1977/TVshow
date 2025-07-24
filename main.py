import os
import sys
import json
import logging
import time
import random
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
        if line.startswith('#EXTINF'):
            extinf_line = line
            info = parse_extinf(extinf_line)
            channel_id = info.get('tvg-id') or info.get('tvg-name') or info.get('title')
            if not channel_id:
                i += 1
                continue
            
            url = None
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line and not next_line.startswith('#'):
                    url = next_line
                    break
                j += 1
            
            if url:
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
        parts = extinf_line.split(',', 1)
        if len(parts) > 1:
            info['title'] = parts[1].strip()
        
        attrs_part = parts[0]
        pattern = r'(\w+[-\w]*)\s*=\s*"([^"]*)"'
        matches = re.findall(pattern, attrs_part)
        for key, value in matches:
            info[key] = value
    except Exception as e:
        logger.error(f"解析EXTINF失败: {str(e)}")
    return info

def download_and_parse_epg(config):
    """下载并解析EPG数据（补充频道图标和信息）"""
    if "epg_urls" not in config or not config["epg_urls"]:
        logger.info("未配置EPG URL，跳过EPG处理")
        return {}
        
    logger.info("开始下载和解析EPG数据")
    epg_data = {}  # {频道ID: {"id": id, "name": name, "icon": icon_url}}
    epg_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Accept": "application/xml,application/xhtml+xml,text/html;q=0.9,text/plain;q=0.8,image/png,*/*;q=0.5"
    }
    
    for epg_url in config["epg_urls"]:
        logger.info(f"下载EPG: {epg_url}")
        try:
            time.sleep(random.uniform(1, 3))
            response = requests.get(epg_url, headers=epg_headers, timeout=120, allow_redirects=True)
            
            if response.status_code != 200:
                logger.error(f"下载EPG失败，状态码: {response.status_code}")
                continue
                
            content = gzip.decompress(response.content) if epg_url.endswith('.gz') else response.content
            root = ET.fromstring(content)
            
            for channel in root.findall(".//channel"):
                channel_id = channel.get('id')
                if not channel_id:
                    continue
                display_name = channel.find('.//display-name')
                name = display_name.text if display_name is not None else ""
                icon = channel.find('.//icon')
                icon_url = icon.get('src') if icon is not None else ""
                
                if channel_id not in epg_data:
                    epg_data[channel_id] = {"id": channel_id, "name": name, "icon": icon_url}
                elif not epg_data[channel_id]["icon"] and icon_url:
                    epg_data[channel_id]["icon"] = icon_url
                    
            logger.info(f"从 {epg_url} 解析出 {len(root.findall('.//channel'))} 个频道信息")
        except Exception as e:
            logger.error(f"处理EPG出错: {str(e)}")
            continue
    
    logger.info(f"EPG数据解析完成，共收集 {len(epg_data)} 个频道信息")
    return epg_data

def categorize_channel(name, config):
    """细化频道分类（新增港澳台细分逻辑）"""
    hk_keywords = ['TVB', '翡翠台', 'ViuTV', 'RTHK', '凤凰', '华丽']
    mo_keywords = ['澳视', '澳门', '莲花']
    tw_keywords = ['台视', '中视', '华视', '中天', '纬来', 'TVBS']
    
    if any(kw in name for kw in hk_keywords):
        return '港澳台频道/香港'
    elif any(kw in name for kw in mo_keywords):
        return '港澳台频道/澳门'
    elif any(kw in name for kw in tw_keywords):
        return '港澳台频道/台湾'
    elif re.match(r'^CCTV-\d+', name):
        return '央视频道'
    elif re.match(r'^(北京|江苏|浙江|湖南|东方)卫视', name):
        return '卫视频道'
    else:
        return '地方频道'  # 可扩展其他分类

def match_channels_with_epg(sources_data, epg_data, config):
    """将频道与EPG数据匹配（补充属性信息）"""
    if not epg_data:
        return sources_data
        
    logger.info("开始匹配频道与EPG数据")
    def simplify_name(name):
        if not name:
            return ""
        simplified = re.sub(r'[^\w\u4e00-\u9fff]', '', name.lower())
        replacements = {'cctv': 'cctv', 'hong': 'hk', 'tai': 'tw', 'television': 'tv'}
        for old, new in replacements.items():
            simplified = simplified.replace(old, new)
        return simplified
    
    epg_index = {}
    for epg_id, data in epg_data.items():
        simple_name = simplify_name(data["name"])
        if simple_name:
            epg_index[simple_name] = epg_id
    
    matched_count = 0
    for channel_id, data in sources_data.items():
        info = data["info"]
        title = info.get('title', '')
        normalized_title = normalize_channel_name(title, config)
        if normalized_title != title:
            info['title'] = normalized_title
        
        # 补充分类信息
        info['group-title'] = categorize_channel(normalized_title, config)
        
        # 补充自定义属性（语言、类型等）
        if normalized_title in config.get("channel_attributes", {}):
            attrs = config["channel_attributes"][normalized_title]
            info.update(attrs)
        
        # EPG匹配逻辑
        if channel_id in epg_data:
            info['tvg-id'] = epg_data[channel_id]["id"]
            if not info.get('tvg-logo') and epg_data[channel_id]["icon"]:
                info['tvg-logo'] = epg_data[channel_id]["icon"]
            matched_count += 1
            continue
        
        simple_title = simplify_name(normalized_title)
        if simple_title in epg_index:
            epg_id = epg_index[simple_title]
            info['tvg-id'] = epg_data[epg_id]["id"]
            if not info.get('tvg-logo') and epg_data[epg_id]["icon"]:
                info['tvg-logo'] = epg_data[epg_id]["icon"]
            matched_count += 1
            continue
    
    logger.info(f"频道与EPG匹配完成，成功匹配 {matched_count} 个频道")
    return sources_data

def normalize_channel_name(name, config):
    """规范化频道名称"""
    if not name:
        return name
    name_lower = name.lower()
    if "channel_name_map" in config:
        for pattern, normalized_name in config["channel_name_map"].items():
            if re.search(pattern, name_lower):
                return normalized_name
    return name

def should_exclude_channel(info, url, config):
    """检查是否排除频道或源"""
    if "excluded_sources" in config:
        for excluded_source in config["excluded_sources"]:
            if excluded_source in url:
                return True
    
    tvg_id = info.get('tvg-id', '')
    if tvg_id and tvg_id.isdigit() and len(tvg_id) < 5:
        return True
    
    group_title = info.get('group-title', '')
    if any(char in group_title for char in ['å', 'é¢', 'è§']):
        return True
    return False

def organize_channels(sources_data, config):
    """整理频道，保留最优源"""
    logger.info("开始整理频道...")
    channels_by_name = {}
    
    for channel_id, data in sources_data.items():
        info = data["info"]
        title = info.get('title', '')
        if not title:
            continue
            
        valid_sources = []
        for source in data["sources"]:
            if source["valid"] and not should_exclude_channel(info, source["url"], config):
                valid_sources.append((source["url"], source["latency"]))
        
        if not valid_sources:
            continue
            
        valid_sources.sort(key=lambda x: x[1])
        best_sources = valid_sources[:min(2, len(valid_sources))]
        
        if title in channels_by_name:
            if best_sources[0][1] < channels_by_name[title]["latency"]:
                channels_by_name[title] = {
                    "info": info,
                    "sources": [source[0] for source in best_sources],
                    "latency": best_sources[0][1]
                }
        else:
            channels_by_name[title] = {
                "info": info,
                "sources": [source[0] for source in best_sources],
                "latency": best_sources[0][1]
            }
    
    logger.info(f"频道整理完成，共 {len(channels_by_name)} 个唯一频道")
    return channels_by_name

def sort_channels_by_category(channels, config):
    """按分类排序（支持多级分类）"""
    category_order = {cat: idx for idx, cat in enumerate(config.get("categories", []))}
    default_order = len(category_order)
    
    # 按分类分组
    categorized = {}
    for name, data in channels.items():
        group = data["info"].get("group-title", "其他")
        if group not in categorized:
            categorized[group] = []
        categorized[group].append((name, data))
    
    # 按配置顺序排序分类
    sorted_groups = sorted(categorized.keys(), key=lambda x: category_order.get(x, default_order))
    
    # 组内排序（央视按数字，其他按名称）
    final_sorted = []
    for group in sorted_groups:
        group_channels = categorized[group]
        if group == "央视频道":
            # 央视频道按数字排序
            group_channels.sort(key=lambda x: int(re.search(r'CCTV-(\d+)', x[0]).group(1)) if re.search(r'CCTV-(\d+)', x[0]) else 0)
        else:
            # 其他按名称拼音排序
            group_channels.sort(key=lambda x: x[0])
        final_sorted.extend(group_channels)
    
    return final_sorted

def generate_m3u(sorted_channels, output_path):
    """生成带分类和多源的M3U文件"""
    logger.info(f"开始生成M3U文件: {output_path}")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for channel_name, data in sorted_channels:
            info = data["info"]
            sources = data["sources"]
            extinf = build_extinf(info)
            f.write(f"{extinf}\n")
            f.write(f"{sources[0]}\n")
            if len(sources) > 1:
                f.write(f"#EXTBURL:{sources[1]}\n")
    
    logger.info(f"M3U文件生成完成: {output_path}, 共 {len(sorted_channels)} 个频道")
    return output_path

def generate_txt(sorted_channels, output_path):
    """生成带分类注释的TXT文件"""
    logger.info(f"开始生成TXT文件: {output_path}")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# 标准IPTV直播源TXT格式：频道名称,主源URL,备用源URL(可选)\n")
        current_group = None
        for channel_name, data in sorted_channels:
            group = data["info"].get("group-title", "其他")
            if group != current_group:
                f.write(f"\n# {group}\n")
                current_group = group
            sources = data["sources"]
            line_parts = [channel_name, sources[0]]
            if len(sources) > 1:
                line_parts.append(sources[1])
            f.write(f"{','.join(line_parts)}\n")
    
    logger.info(f"TXT文件生成完成: {output_path}, 共 {len(sorted_channels)} 个频道")
    return output_path

def build_extinf(info):
    """构建包含分类和属性的EXTINF行"""
    attrs = []
    for key, value in info.items():
        if key != 'title':
            attrs.append(f'{key}="{value}"')
    attrs_str = ' '.join(attrs)
    title = info.get('title', '')
    return f"#EXTINF:-1 {attrs_str},{title}"

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='IPTV直播源处理工具')
    parser.add_argument('--no-check', action='store_true', help='跳过直播源检测')
    parser.add_argument('--no-epg', action='store_true', help='跳过EPG处理')
    parser.add_argument('--max-channels', type=int, default=0, help='最大处理频道数(测试用)')
    args = parser.parse_args()
    
    start_time = time.time()
    logger.info("开始IPTV直播源处理流程")
    
    try:
        config = load_config()
        output_dir = os.path.join(os.path.dirname(__file__), config["output_dir"])
        os.makedirs(output_dir, exist_ok=True)
        
        # 收集直播源
        collector = IPTVSourceCollector(config)
        source_files = collector.collect()
        logger.info(f"直播源收集完成，共 {len(source_files)} 个文件")
        
        # 解析所有源文件
        all_channels = {}
        for filepath in source_files:
            channels = parse_m3u_file(filepath)
            for channel_id, (info, urls) in channels.items():
                if channel_id in all_channels:
                    all_channels[channel_id][1].extend(urls)
                else:
                    all_channels[channel_id] = [info, urls]
        
        # 转换为检测格式
        sources_data = {}
        for channel_id, (info, urls) in all_channels.items():
            sources_data[channel_id] = {
                "info": info,
                "urls": list(set(urls))  # 去重URL
            }
        
        # 检测直播源有效性
        if not args.no_check:
            checker = IPTVSourceChecker(config)
            sources_data = checker.check(sources_data)
        else:
            for channel_id in sources_data:
                sources_data[channel_id]["sources"] = [
                    {"url": url, "valid": True, "latency": 0} for url in sources_data[channel_id]["urls"]
                ]
        
        # 处理EPG
        epg_data = {} if args.no_epg else download_and_parse_epg(config)
        sources_data = match_channels_with_epg(sources_data, epg_data, config)
        
        # 整理频道
        organized_channels = organize_channels(sources_data, config)
        
        # 按分类排序
        sorted_channels = sort_channels_by_category(organized_channels, config)
        
        # 生成输出文件
        m3u_path = os.path.join(output_dir, "iptv_collection.m3u")
        txt_path = os.path.join(output_dir, "iptv_collection.txt")
        generate_m3u(sorted_channels, m3u_path)
        generate_txt(sorted_channels, txt_path)
        
        logger.info(f"所有处理完成，耗时 {time.time() - start_time:.2f} 秒")
        
    except Exception as e:
        logger.error(f"处理过程出错: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
