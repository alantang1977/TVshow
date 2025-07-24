#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import requests
import time
import random
import re
import ssl
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("IPTV-Collector")

class IPTVSourceCollector:
    def __init__(self, config):
        self.config = config
        self.sources_dir = os.path.join(os.path.dirname(__file__), "data", "sources")
        os.makedirs(self.sources_dir, exist_ok=True)
        
        # 多样化User-Agent列表，模拟不同浏览器和设备
        self.user_agents = [
            # 桌面浏览器 - Chrome
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            
            # 桌面浏览器 - Firefox
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:126.0) Gecko/20100101 Firefox/126.0",
            "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
            
            # 桌面浏览器 - Safari
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
            
            # 移动设备 - Chrome
            "Mozilla/5.0 (Linux; Android 14; SM-S908E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/125.0.6422.80 Mobile/15E148 Safari/604.1",
            
            # 移动设备 - Safari
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
            
            # 搜索引擎爬虫模拟（温和型）
            "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Mozilla/5.0 (compatible; Bingbot/2.0; +http://www.bing.com/bingbot.htm)"
        ]
        
        # 多样化Referer列表
        self.referers = [
            "https://www.google.com/",
            "https://www.bing.com/",
            "https://www.baidu.com/",
            "https://github.com/",
            "https://www.youtube.com/",
            "https://m.baidu.com/",
            "https://news.baidu.com/",
            "https://www.sohu.com/",
            "https://www.sina.com.cn/",
            ""  # 空referer
        ]
        
        # SSL协议版本列表，用于处理握手失败
        self.ssl_versions = [
            ssl.PROTOCOL_TLS_CLIENT,
            ssl.PROTOCOL_TLSv1_2,
            ssl.PROTOCOL_TLSv1_3
        ]

    def collect(self):
        """收集所有配置的直播源"""
        logger.info("开始收集直播源...")
        
        collected_files = []
        
        # 使用线程池并发下载，线程数从配置获取或默认8
        max_workers = self.config.get("max_workers", 8)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
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
            
            # 尝试多次下载，每次使用不同的请求头组合
            max_attempts = self.config.get("max_attempts", 3)
            for attempt in range(max_attempts):
                # 随机选择User-Agent和Referer
                headers = self._get_random_headers()
                
                # 添加随机延迟避免被识别为机器人，延迟时间随重试递增
                delay = random.uniform(1 + attempt, 3 + attempt * 2)
                logger.debug(f"第 {attempt+1} 次尝试，延迟 {delay:.2f} 秒")
                time.sleep(delay)
                
                try:
                    # 尝试不同的SSL协议版本
                    ssl_version = random.choice(self.ssl_versions)
                    response = self._make_request(source_url, headers, ssl_version, verify=True)
                    
                    # 检查状态码
                    if response.status_code == 200:
                        return self._process_valid_response(response, local_path, source_url)
                    elif response.status_code in [403, 404, 503, 525]:
                        logger.warning(f"下载源尝试 {attempt + 1}/{max_attempts} 失败: {source_url}, 状态码: {response.status_code}")
                        # 针对525错误，尝试跳过证书验证
                        if response.status_code == 525:
                            logger.info(f"尝试跳过证书验证重新连接: {source_url}")
                            response = self._make_request(source_url, headers, ssl_version, verify=False)
                            if response.status_code == 200:
                                return self._process_valid_response(response, local_path, source_url)
                        
                        if attempt == max_attempts - 1:  # 最后一次尝试失败
                            logger.error(f"所有尝试均失败: {source_url}, 状态码: {response.status_code}")
                            return None
                        continue  # 继续尝试
                    else:
                        logger.error(f"下载源失败: {source_url}, 状态码: {response.status_code}")
                        return None
                        
                except requests.exceptions.SSLError as e:
                    logger.warning(f"SSL错误 (尝试 {attempt + 1}/{max_attempts}): {source_url}, 错误: {str(e)}")
                    # 尝试跳过证书验证
                    try:
                        logger.info(f"尝试跳过证书验证: {source_url}")
                        response = self._make_request(source_url, headers, ssl_version, verify=False)
                        if response.status_code == 200:
                            return self._process_valid_response(response, local_path, source_url)
                    except Exception as e2:
                        logger.error(f"跳过证书验证后仍失败: {str(e2)}")
                    
                    if attempt == max_attempts - 1:
                        logger.error(f"所有SSL尝试均失败: {source_url}")
                        return None
                    continue
                    
                except requests.exceptions.RequestException as e:
                    logger.warning(f"下载源尝试 {attempt + 1}/{max_attempts} 出错: {source_url}, 错误: {str(e)}")
                    if attempt == max_attempts - 1:
                        logger.error(f"所有尝试均出错: {source_url}, 错误: {str(e)}")
                        return None
                    continue
        
        except Exception as e:
            logger.error(f"处理源失败: {source_url}, 错误: {str(e)}")
            return None
    
    def _make_request(self, url, headers, ssl_version, verify):
        """创建请求并处理SSL配置"""
        # 创建自定义SSL上下文
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = verify
        ssl_context.verify_mode = ssl.CERT_REQUIRED if verify else ssl.CERT_NONE
        
        try:
            ssl_context.options |= ssl_version
        except ValueError:
            # 某些环境可能不支持特定SSL版本
            pass
        
        return requests.get(
            url,
            headers=headers,
            timeout=self.config.get("timeout", 30),
            allow_redirects=True,
            verify=verify,
            ssl_context=ssl_context if verify else None
        )
    
    def _get_random_headers(self):
        """生成随机请求头组合"""
        headers = {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": random.choice([
                "zh-CN,zh;q=0.9,en;q=0.8",
                "zh-CN,zh;q=0.9",
                "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"
            ]),
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": random.choice(["keep-alive", "close"]),
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": random.choice(["max-age=0", "no-cache", "max-age=300"]),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": random.choice(["none", "same-origin", "cross-site"]),
            "Sec-Fetch-User": "?1"
        }
        
        # 随机添加Referer
        referer = random.choice(self.referers)
        if referer:
            headers["Referer"] = referer
            
        # 随机添加DNT (Do Not Track)
        if random.random() > 0.5:
            headers["DNT"] = "1"
            
        # 随机添加Accept-CH (客户端提示)
        if random.random() > 0.7:
            headers["Accept-CH"] = "Sec-CH-UA,Sec-CH-UA-Mobile,Sec-CH-UA-Platform"
            
        return headers
    
    def _process_valid_response(self, response, local_path, source_url):
        """处理有效的响应内容"""
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
        safe_filename = re.sub(r'[^\w.-]', '_', safe_filename)
        
        return safe_filename
        
    def _is_txt_channel_list(self, content):
        """检查内容是否为txt格式的频道列表"""
        if not content:
            return False
            
        # 简单检查是否包含URL模式
        lines = content.strip().split('\n')
        
        # 检查至少有一行符合常见直播源URL模式
        url_patterns = [r'https?://', r'rtmp://', r'rtsp://', r'udp://', r'mms://']
        
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
            if re.match(r'https?://|rtmp://|rtsp://|udp://|mms://', line):
                m3u_content += f"#EXTINF:-1,Unknown Channel\n{line}\n"
            elif ',' in line:
                # 可能是"频道名,URL"格式
                parts = line.split(',', 1)
                if len(parts) == 2 and re.match(r'https?://|rtmp://|rtsp://|udp://|mms://', parts[1].strip()):
                    channel_name = parts[0].strip()
                    url = parts[1].strip()
                    m3u_content += f"#EXTINF:-1,{channel_name}\n{url}\n"
                else:
                    continue
            else:
                continue
        
        return m3u_content
