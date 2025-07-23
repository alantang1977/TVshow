#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import subprocess
import json
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm

logger = logging.getLogger("IPTV-Checker")

class IPTVSourceChecker:
    def __init__(self, config):
        self.config = config
        self.results = {}  # 格式: {频道ID: {"info": info, "sources": [(URL, 是否有效, 延迟)]}}
        
    def check(self, channels):
        """检查所有频道的所有源的有效性"""
        logger.info(f"开始检查 {len(channels)} 个频道的直播源...")
        
        # 准备检查任务
        check_tasks = []
        for channel_id, (info, urls) in channels.items():
            for url in urls:
                check_tasks.append((channel_id, info, url))
        
        logger.info(f"共 {len(check_tasks)} 个直播源需要检查")
        
        # 使用线程池并发检查
        with ThreadPoolExecutor(max_workers=self.config["max_workers"]) as executor:
            futures = {executor.submit(self._check_source, task[2]): task for task in check_tasks}
            
            # 显示进度条
            with tqdm(total=len(futures), desc="检查直播源") as pbar:
                for future in futures:
                    channel_id, info, url = futures[future]
                    try:
                        is_valid, latency = future.result()
                        
                        # 存储结果
                        if channel_id not in self.results:
                            self.results[channel_id] = {
                                "info": info,
                                "sources": []
                            }
                        
                        self.results[channel_id]["sources"].append((url, is_valid, latency))
                            
                    except Exception as e:
                        logger.error(f"检查任务失败: {channel_id}, {url}, 错误: {str(e)}")
                        
                        # 添加失败记录
                        if channel_id not in self.results:
                            self.results[channel_id] = {
                                "info": info,
                                "sources": []
                            }
                        self.results[channel_id]["sources"].append((url, False, float('inf')))
                    finally:
                        pbar.update(1)
        
        # 统计检查结果
        total_channels = len(self.results)
        valid_channels = sum(1 for channel_id, result in self.results.items() 
                            if any(is_valid for _, is_valid, _ in result["sources"]))
        total_sources = sum(len(result["sources"]) for result in self.results.values())
        valid_sources = sum(sum(1 for _, is_valid, _ in result["sources"] if is_valid) 
                           for result in self.results.values())
        
        logger.info("直播源检查完成")
        logger.info(f"频道统计: {valid_channels}/{total_channels} 个频道有效")
        logger.info(f"直播源统计: {valid_sources}/{total_sources} 个直播源有效")
        
        return self.results
    
    def _check_source(self, url):
        """检查单个源是否有效，返回(是否有效, 延迟)"""
        try:
            start_time = time.time()
            
            # 使用ffprobe检查流
            cmd = [
                'ffprobe', 
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-i', url
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=self.config["check_timeout"])
            
            end_time = time.time()
            latency = end_time - start_time
            
            # 检查是否成功
            if result.returncode == 0:
                # 解析JSON输出
                try:
                    output = result.stdout.decode('utf-8', errors='ignore')
                    stream_info = json.loads(output) if output else {}
                    
                    # 检查是否包含视频流
                    has_video = False
                    if 'streams' in stream_info:
                        for stream in stream_info['streams']:
                            if stream.get('codec_type') == 'video':
                                has_video = True
                                break
                    
                    is_valid = has_video
                    
                    if is_valid:
                        logger.debug(f"有效源: {url}, 延迟: {latency:.2f}秒")
                        return True, latency
                    else:
                        logger.debug(f"无效源(无视频流): {url}")
                        return False, float('inf')
                        
                except json.JSONDecodeError:
                    logger.debug(f"无效源(JSON解析失败): {url}")
                    return False, float('inf')
            else:
                logger.debug(f"无效源(FFprobe失败): {url}")
                return False, float('inf')
                
        except subprocess.TimeoutExpired:
            logger.debug(f"检查超时: {url}")
            return False, float('inf')
        except Exception as e:
            logger.debug(f"检查出错: {url}, 错误: {str(e)}")
            return False, float('inf')
