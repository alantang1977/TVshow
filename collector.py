#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import requests
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("IPTV-Collector")

class IPTVSourceCollector:
    def __init__(self, config):
        self.config = config
        self.sources_dir = os.path.join(os.path.dirname(__file__), "data", "sources")
        os.makedirs(self.sources_dir, exist_ok=True)
        
    def collect(self):
        """收集所有配置的直播源"""
        logger.info("开始收集直播源...")
        
        collected_files = []
        
        # 使用线程池并发下载
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {}
            
            for source_url in self.config["sources"]:
                future = executor.submit(self._download_source, source_url)
                futures[future] = source_url
            
            # 收集结果
            for future in futures:
                source_url = futures[future]
                try:
                    result = future.result()
                    if result:
                        collected_files.append(result)
                except Exception as e:
                    logger.error(f"下载源失败: {source_url}, 错误: {str(e)}")
        
        logger.info(f"收集完成, 共 {len(collected_files)} 个文件")
        return collected_files
    
    def _download_source(self, source_url):
        """下载单个源，返回本地文件路径或None"""
        try:
            # 获取源文件名
            filename = self._get_filename_from_url(source_url)
            local_path = os.path.join(self.sources_dir, filename)
            
            # 下载源文件
            logger.info(f"下载源: {source_url}")
            
            headers = {
                "User-Agent": self.config.get("user_agent", "Mozilla/5.0"),
                "Accept": "*/*"
            }
            
            response = requests.get(
                source_url,
                headers=headers,
                timeout=30,
                allow_redirects=True
            )
            
            if response.status_code == 200:
                content = response.text
                
                # 检查是否是有效的m3u/txt文件
                if not content or (
                    not content.strip().startswith('#EXTM3U') and 
                    not self._is_txt_channel_list(content)
                ):
                    logger.warning(f"无效的直播源文件: {source_url}")
                    return None
                
                # 如果是txt格式但包含频道列表，转换为m3u格式
                if not content.strip().startswith('#EXTM3U') and self._is_txt_channel_list(content):
                    content = self._convert_txt_to_m3u(content)
                
                # 保存文件
                with open(local_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                
                logger.info(f"成功下载源到: {local_path}")
                return local_path
            else:
                logger.error(f"下载源失败: {source_url}, 状态码: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"处理源失败: {source_url}, 错误: {str(e)}")
            return None
    
    def _get_filename_from_url(self, url):
        """从URL中获取文件名"""
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        
        # 提取文件名
        filename = os.path.basename(path)
        
        # 如果没有扩展名
        if not filename or '.' not in filename:
            filename = f"source_{int(time.time())}.m3u"
            
        # 添加域名前缀以避免冲突
        domain = parsed.netloc.split('.')[-2] if len(parsed.netloc.split('.')) > 1 else parsed.netloc
        domain = domain.replace('-', '_').replace('.', '_')
        timestamp = int(time.time())
        safe_filename = f"{domain}_{timestamp}_{filename}"
        
        # 确保文件名安全
        import re
        safe_filename = re.sub(r'[^\w.-]', '_', safe_filename)
        
        return safe_filename
        
    def _is_txt_channel_list(self, content):
        """检查内容是否为txt格式的频道列表"""
        if not content:
            return False
            
        # 简单检查是否包含URL模式
        import re
        lines = content.strip().split('\n')
        
        # 检查至少有一行符合常见直播源URL模式
        url_patterns = [r'https?://', r'rtmp://', r'rtsp://']
        
        for line in lines[:20]:  # 只检查前20行
            line = line.strip()
            if any(re.search(pattern, line) for pattern in url_patterns):
                return True
                
        return False
        
    def _convert_txt_to_m3u(self, content):
        """将txt格式的频道列表转换为m3u格式"""
        lines = content.strip().split('\n')
        m3u_content = "#EXTM3U\n"
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # 检查是否为URL
            import re
            if re.match(r'https?://|rtmp://|rtsp://', line):
                m3u_content += f"#EXTINF:-1,Unknown Channel\n{line}\n"
            elif ',' in line:
                # 可能是"频道名,URL"格式
                parts = line.split(',', 1)
                if len(parts) == 2 and re.match(r'https?://|rtmp://|rtsp://', parts[1].strip()):
                    channel_name = parts[0].strip()
                    url = parts[1].strip()
                    m3u_content += f"#EXTINF:-1,{channel_name}\n{url}\n"
                else:
                    continue
            else:
                continue
        
        return m3u_content
