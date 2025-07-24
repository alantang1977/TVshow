import os
import re
import requests
import logging
import time
import random

logger = logging.getLogger("IPTV-Collector")

class IPTVSourceCollector:
    def __init__(self, config):
        self.config = config
        self.source_dir = config.get("source_files", "data/sources")
        os.makedirs(self.source_dir, exist_ok=True)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        }

    def collect(self):
        """收集所有配置的直播源"""
        source_files = []
        for idx, url in enumerate(self.config.get("sources", [])):
            try:
                logger.info(f"正在收集直播源: {url}")
                time.sleep(random.uniform(1, 3))  # 随机延迟防封禁
                response = requests.get(url, headers=self.headers, timeout=30)
                response.encoding = 'utf-8'
                
                if response.status_code != 200:
                    logger.warning(f"获取源失败，状态码: {response.status_code}, URL: {url}")
                    continue
                
                # 保存原始内容
                filename = f"source_{idx}_{hash(url)}.m3u"
                filepath = os.path.join(self.source_dir, filename)
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(response.text)
                
                # 转换非M3U格式为标准M3U
                if not response.text.startswith('#EXTM3U'):
                    converted = self._convert_to_m3u(response.text)
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(converted)
                
                source_files.append(filepath)
                logger.info(f"成功保存直播源: {filepath}")
                
            except Exception as e:
                logger.error(f"收集直播源失败 {url}: {str(e)}")
                continue
        
        return source_files

    def _convert_to_m3u(self, content):
        """将TXT等格式转换为标准M3U"""
        lines = content.strip().split('\n')
        m3u_content = "#EXTM3U\n"
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            if re.match(r'https?://|rtmp://|rtsp://', line):
                m3u_content += f"#EXTINF:-1,Unknown Channel\n{line}\n"
            elif ',' in line:
                parts = line.split(',', 1)
                if len(parts) == 2 and re.match(r'https?://|rtmp://|rtsp://', parts[1].strip()):
                    channel_name = parts[0].strip()
                    url = parts[1].strip()
                    m3u_content += f"#EXTINF:-1,{channel_name}\n{url}\n"
        
        return m3u_content
