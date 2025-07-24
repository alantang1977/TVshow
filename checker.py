import os
import time
import logging
import requests
import subprocess
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("IPTV-Checker")

class IPTVSourceChecker:
    def __init__(self, config):
        self.config = config
        self.timeout = 10  # 检测超时时间(秒)
        self.max_workers = 50  # 并发数

    def check(self, sources_data):
        """批量检测直播源有效性"""
        logger.info(f"开始检测直播源，共 {len(sources_data)} 个频道")
        
        # 并发检测所有频道
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for channel_id, data in sources_data.items():
                futures.append(executor.submit(self._check_channel, channel_id, data))
            
            # 收集结果
            results = {}
            for future in futures:
                channel_id, result = future.result()
                results[channel_id] = result
        
        return results

    def _check_channel(self, channel_id, data):
        """检测单个频道的所有源"""
        info = data["info"]
        urls = data["urls"]
        results = []
        
        for url in urls:
            try:
                start_time = time.time()
                # 优先使用ffmpeg检测（更准确）
                if self._check_with_ffmpeg(url):
                    latency = time.time() - start_time
                    results.append({"url": url, "valid": True, "latency": latency})
                else:
                    # 备用：HTTP头部检测
                    response = requests.head(url, timeout=self.timeout, allow_redirects=True)
                    if 200 <= response.status_code < 400:
                        latency = time.time() - start_time
                        results.append({"url": url, "valid": True, "latency": latency})
                    else:
                        results.append({"url": url, "valid": False, "latency": float('inf')})
            except Exception as e:
                results.append({"url": url, "valid": False, "latency": float('inf')})
        
        return channel_id, {"info": info, "sources": results}

    def _check_with_ffmpeg(self, url):
        """使用ffmpeg检测流有效性"""
        try:
            cmd = [
                "ffmpeg",
                "-v", "error",
                "-i", url,
                "-t", "1",  # 只检测1秒
                "-f", "null", "-"
            ]
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, Exception):
            return False
