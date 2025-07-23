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
    if tvg_id and tvg_id.isdigit() and len(tvg_id) < 5:  # 排除类似"4"这样的频道ID
        return True
        
    # 检查组标题是否包含乱码
    group_title = info.get('group-title', '')
    if any(char in group_title for char in ['å', 'é¢', 'è§', 'é', '¢', '§', 'è', 'æ', 'ç', '¾', 'â']):
        return True
    
    return False

def organize_channels(sources_data, config):
    """整理频道，去除重复，为每个频道保留最多两个源"""
    logger.info("开始整理频道...")
    
    # 按频道名称和分类分组
    channels_by_name = {}
    
    # 整理频道
    for channel_id, data in sources_data.items():
        info = data["info"]
        title = info.get('title', '')
        
        # 跳过没有标题的频道
        if not title:
            continue
            
        # 获取或自动分类
        category = info.get('group-title') or classify_channel(info, config)
        info['group-title'] = category  # 确保分类信息存在
        
        # 收集有效源，并排除不需要的源
        valid_sources = []
        for source in data["sources"]:
            if source["valid"] and not should_exclude_channel(info, source["url"], config):
                valid_sources.append((source["url"], source["latency"]))
        
        # 如果没有有效源，跳过此频道
        if not valid_sources:
            continue
            
        # 按延迟排序
        valid_sources.sort(key=lambda x: x[1])
        
        # 保留最多两个源（速度最快和第二快的）
        best_sources = valid_sources[:min(2, len(valid_sources))]
        
        # 将频道添加到按名称和分类分组的集合中
        if title in channels_by_name:
            existing_sources = channels_by_name[title]["sources"]
            existing_latency = channels_by_name[title]["latency"]
            
            # 如果现有的延迟更高（更慢），则替换为新的源
            if best_sources[0][1] < existing_latency:
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

def classify_channel(info, config):
    """自动分类频道"""
    title = info.get('title', '').lower()
    
    # 检查是否为央视频道
    if re.search(r'cctv|央视', title):
        return '央视频道'
    # 检查是否为卫视频道
    elif re.search(r'卫视|卫星', title) and not re.search(r'cctv', title):
        return '卫视频道'
    # 检查是否为地方频道
    elif re.search(r'北京|上海|广东|江苏|浙江|湖南|山东|四川|重庆|天津', title):
        return '地方频道'
    # 检查是否为体育频道
    elif re.search(r'体育|赛事|奥运|足球|篮球', title):
        return '体育频道'
    # 检查是否为影视频道
    elif re.search(r'电影|剧集|影视', title):
        return '影视频道'
    # 检查是否为新闻频道
    elif re.search(r'新闻|资讯', title):
        return '新闻频道'
    # 检查是否为少儿频道
    elif re.search(r'少儿|卡通|动画', title):
        return '少儿频道'
    # 其他频道
    else:
        return '其他频道'

def sort_channels_by_category(channels, config):
    """按分类对频道进行排序，央视频道按数字从小到大排序"""
    # 定义分类顺序
    category_order = {cat: idx for idx, cat in enumerate(config.get("categories", ["央视频道", "卫视频道", "地方频道", "体育频道", "影视频道", "新闻频道", "少儿频道", "其他频道"]))}
    default_order = len(category_order)
    
    # 按分类分组
    categorized_channels = {}
    for channel_name, data in channels.items():
        category = data["info"].get("group-title", "其他频道")
        if category not in categorized_channels:
            categorized_channels[category] = []
        categorized_channels[category].append((channel_name, data))
    
    # 对每个分类内的频道进行排序
    sorted_categories = {}
    for category, channel_list in categorized_channels.items():
        if category == "央视频道":
            # 央视频道按数字排序
            sorted_list = sorted(channel_list, key=lambda x: extract_cctv_number(x[0]))
        else:
            # 其他分类按名称排序
            sorted_list = sorted(channel_list, key=lambda x: x[0])
        sorted_categories[category] = sorted_list
    
    # 按分类顺序整理最终列表
    final_sorted = []
    # 先添加配置中定义的分类
    for cat in config.get("categories", []):
        if cat in sorted_categories:
            final_sorted.extend(sorted_categories[cat])
            del sorted_categories[cat]
    # 再添加剩余分类
    for cat in sorted(sorted_categories.keys()):
        final_sorted.extend(sorted_categories[cat])
    
    return final_sorted

def extract_cctv_number(channel_name):
    """提取CCTV频道的数字用于排序"""
    match = re.search(r'CCTV-?(\d+)', channel_name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 999  # 非数字频道排在后面

def generate_m3u(sorted_channels, output_path):
    """生成标准M3U文件，使用group-title标记分类，格式如：#EXTINF:-1 group-title="央视频道",CCTV1"""
    logger.info(f"开始生成M3U文件: {output_path}")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        # 写入头部
        f.write("#EXTM3U\n")
        
        # 写入频道信息
        for channel_name, data in sorted_channels:
            info = data["info"]
            sources = data["sources"]
            
            # 获取分类信息
            category = info.get('group-title', '其他频道')
            
            # 构建简化的EXTINF行，只包含group-title和频道名称
            extinf = f'#EXTINF:-1 group-title="{category}",{channel_name}'
            f.write(f"{extinf}\n")
            
            # 写入主源
            f.write(f"{sources[0]}\n")
            
            # 如果有备用源，添加备用源标记和URL
            if len(sources) > 1:
                f.write(f"#EXTBURL:{sources[1]}\n")
    
    logger.info(f"M3U文件生成完成: {output_path}, 共 {len(sorted_channels)} 个频道")
    return output_path

def generate_txt(sorted_channels, output_path):
    """生成标准TXT格式直播源文件，使用"分类名称,#genre#"作为分类标记"""
    logger.info(f"开始生成TXT文件: {output_path}")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        # 写入说明行
        f.write("# 标准IPTV直播源TXT格式：\n")
        f.write("# 1. 分类行格式：分类名称,#genre#\n")
        f.write("# 2. 频道行格式：频道名称,主源URL,备用源URL(可选)\n\n")
        
        current_category = None
        # 写入频道信息
        for channel_name, data in sorted_channels:
            category = data["info"].get("group-title", "其他频道")
            
            # 如果进入新分类，写入分类标记行
            if category != current_category:
                # 除了第一个分类外，在分类前加空行分隔
                if current_category is not None:
                    f.write("\n")
                f.write(f"{category},#genre#\n")
                current_category = category
            
            # 构建频道行
            sources = data["sources"]
            line_parts = [channel_name, sources[0]]
            # 添加备用源（如果有）
            if len(sources) > 1:
                line_parts.append(sources[1])
            # 写入行
            f.write(f"{','.join(line_parts)}\n")
    
    logger.info(f"TXT文件生成完成: {output_path}, 共 {len(sorted_channels)} 个频道")
    return output_path

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='IPTV直播源收集、检测与整理工具')
    parser.add_argument('--no-check', action='store_true', help='跳过直播源检测步骤')
    parser.add_argument('--no-epg', action='store_true', help='跳过EPG处理')
    parser.add_argument('--max-channels', type=int, default=0, help='最大处理频道数量(用于测试)')
    args = parser.parse_args()
    
    start_time = time.time()
    logger.info("开始IPTV直播源处理流程")
    
    try:
        # 加载配置
        config = load_config()
        logger.info(f"配置加载完成，共 {len(config.get('sources', []))} 个直播源")
        
        # 创建输出目录（使用配置中的output_dir，不拼接脚本目录）
        output_dir = config.get("output_dir", "output")
        os.makedirs(output_dir, exist_ok=True)
        
        # 收集直播源
        collector = IPTVSourceCollector(config)
        source_files = collector.collect()
        logger.info(f"直播源收集完成，共 {len(source_files)} 个文件")
        
        # 解析所有源文件
        all_channels = {}
        for filepath in source_files:
            channels = parse_m3u_file(filepath)
            
            # 合并到全局频道集合
            for channel_id, (info, urls) in channels.items():
                if channel_id in all_channels:
                    all_channels[channel_id][1].extend(urls)
                else:
                    all_channels[channel_id] = [info, urls]
                    
            # 如果设置了最大频道数限制，用于测试
            if args.max_channels > 0 and len(all_channels) >= args.max_channels:
                logger.info(f"达到最大频道数限制 ({args.max_channels})，停止收集")
                break
        
        # 去重URL
        for channel_id, (info, urls) in all_channels.items():
            unique_urls = list({url.strip() for url in urls if url.strip()})
            all_channels[channel_id][1] = unique_urls
        
        logger.info(f"去重后共 {len(all_channels)} 个频道，准备进行处理")
        
        # 转换为检测所需的格式
        sources_data = {}
        for channel_id, (info, urls) in all_channels.items():
            sources_data[channel_id] = {
                "info": info,
                "sources": [{"url": url, "valid": True, "latency": 9999} for url in urls]
            }
        
        # 检测直播源（如果未禁用）
        if not args.no_check:
            checker = IPTVSourceChecker(config)
            sources_data = checker.check(sources_data)
        
        # 处理EPG数据（如果未禁用）
        epg_data = {}
        if not args.no_epg:
            epg_data = download_and_parse_epg(config)
        
        # 匹配频道与EPG
        sources_data = match_channels_with_epg(sources_data, epg_data, config)
        
        # 整理频道
        organized_channels = organize_channels(sources_data, config)
        
        # 按分类排序频道
        sorted_channels = sort_channels_by_category(organized_channels, config)
        
        # 生成输出文件路径（基于配置的output_file）
        base_filename = os.path.splitext(config.get("output_file", "iptv.m3u"))[0]
        m3u_path = os.path.join(output_dir, f"{base_filename}.m3u")
        txt_path = os.path.join(output_dir, f"{base_filename}.txt")
        
        # 生成M3U文件
        generate_m3u(sorted_channels, m3u_path)
        
        # 生成TXT文件
        generate_txt(sorted_channels, txt_path)
        
        # 计算总耗时
        end_time = time.time()
        logger.info(f"所有处理完成，总耗时: {end_time - start_time:.2f}秒")
        
    except Exception as e:
        logger.error(f"处理过程出错: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
