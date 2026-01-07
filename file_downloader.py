#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件下载器 - 下载 .onion 站点的文件（支持目录索引）

用法：
    python file_downloader.py <目标URL> [文件类型过滤]

示例：
    python file_downloader.py http://xxx.onion/DEF%20CON%201/ pdf,mp4,zip
"""
import re
import os
import asyncio
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from engine import OnionEngine

import warnings
from bs4 import XMLParsedAsHTMLWarning
warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)


class FileDownloader:
    """文件下载器（支持目录索引站）"""
    
    # 常见文件扩展名
    FILE_EXTENSIONS = {
        'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
        'zip', 'rar', '7z', 'tar', 'gz', 'zst',
        'mp4', 'mp3', 'avi', 'mkv', 'wav', 'flac',
        'jpg', 'jpeg', 'png', 'gif', 'bmp',
        'txt', 'md', 'csv', 'json', 'xml',
        'exe', 'msi', 'dmg', 'iso',
        'py', 'c', 'cpp', 'h', 'java', 'js',
    }
    
    def __init__(self, base_url: str, output_dir: str = None, file_types: set = None):
        self.base_url = base_url.rstrip('/')
        self.base_domain = urlparse(base_url).netloc
        self.engine = OnionEngine()
        
        # 文件类型过滤
        self.file_types = file_types or self.FILE_EXTENSIONS
        
        # 输出目录
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = Path(f"downloaded-{self.base_domain[:16]}")
        self.output_dir.mkdir(exist_ok=True)
        
        # 状态
        self.visited_dirs = set()
        self.downloaded_files = set()
        self.dir_queue = []
        self.file_queue = []
        
        # 统计
        self.total_size = 0
        self.file_count = 0
        
        # 已下载文件列表（断点续传）
        self.done_file = self.output_dir / '.downloaded.txt'
        self._load_done()
    
    def _load_done(self):
        """加载已下载文件列表"""
        if self.done_file.exists():
            with open(self.done_file, 'r', encoding='utf-8') as f:
                self.downloaded_files = set(line.strip() for line in f)
            print(f"[*] 已下载 {len(self.downloaded_files)} 个文件，跳过")
    
    def _save_done(self, url: str):
        """记录已下载文件"""
        with open(self.done_file, 'a', encoding='utf-8') as f:
            f.write(url + '\n')
        self.downloaded_files.add(url)
    
    def _is_file_url(self, url: str) -> bool:
        """判断是否是文件链接"""
        path = urlparse(url).path.lower()
        ext = path.split('.')[-1] if '.' in path else ''
        return ext in self.file_types
    
    def _is_directory_url(self, url: str) -> bool:
        """判断是否是目录链接"""
        path = urlparse(url).path
        return path.endswith('/') or '.' not in path.split('/')[-1]
    
    def _url_to_filepath(self, url: str) -> Path:
        """URL 转本地文件路径"""
        parsed = urlparse(url)
        # 解码 URL 编码的路径
        path = unquote(parsed.path).strip('/')
        # 只保留 base_url 之后的相对路径
        base_path = unquote(urlparse(self.base_url).path).strip('/')
        if path.startswith(base_path):
            path = path[len(base_path):].strip('/')
        return self.output_dir / path
    
    def _extract_links(self, html: str, base_url: str) -> tuple:
        """提取目录和文件链接（只提取子目录，不向上递归）"""
        dirs = set()
        files = set()
        
        base_path = urlparse(self.base_url).path.rstrip('/')
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                # 跳过父目录、锚点、查询参数、绝对路径
                if href.startswith(('javascript:', 'mailto:', '#', '?', '/', 'http://', 'https://')):
                    continue
                # 跳过 Parent Directory 链接
                if href == '../' or href.startswith('..') or 'parent' in a.get_text().lower():
                    continue
                
                # 处理相对路径 - base_url 已经以 / 结尾
                full_url = urljoin(base_url, href)
                full_path = urlparse(full_url).path
                
                # 只处理同站点
                if urlparse(full_url).netloc != self.base_domain:
                    continue
                
                # 关键：只处理 base_url 的子路径，不向上递归
                if not full_path.startswith(base_path):
                    continue
                
                if self._is_file_url(full_url):
                    files.add(full_url)
                elif href.endswith('/'):  # 明确是目录
                    dirs.add(full_url)
        except:
            pass
        
        return dirs, files
    
    async def scan_directory(self, url: str) -> tuple:
        """扫描目录，返回子目录和文件"""
        if url in self.visited_dirs:
            return set(), set()
        
        # 确保 URL 以 / 结尾
        if not url.endswith('/'):
            url = url + '/'
        
        try:
            status, content_type, body = await self.engine.fetch(url, timeout=60)
            html = body.decode('utf-8', errors='ignore')
            self.visited_dirs.add(url)
            
            dirs, files = self._extract_links(html, url)
            print(f"[D] {unquote(url[-60:])} -> {len(dirs)} 目录, {len(files)} 文件")
            return dirs, files
            
        except Exception as e:
            print(f"[✗] 扫描失败: {url[-50:]} - {str(e)[:30]}")
            return set(), set()
    
    async def download_file(self, url: str, semaphore: asyncio.Semaphore) -> bool:
        """下载单个文件"""
        async with semaphore:
            if url in self.downloaded_files:
                return True
            
            filepath = self._url_to_filepath(url)
            
            # 如果文件已存在，跳过
            if filepath.exists():
                self._save_done(url)
                return True
            
            try:
                status, content_type, body = await self.engine.fetch(url, timeout=120)
                
                # 创建目录
                filepath.parent.mkdir(parents=True, exist_ok=True)
                
                # 保存文件
                with open(filepath, 'wb') as f:
                    f.write(body)
                
                size_kb = len(body) / 1024
                self.total_size += len(body)
                self.file_count += 1
                self._save_done(url)
                
                print(f"[↓] {filepath.name} ({size_kb:.1f} KB)")
                return True
                
            except asyncio.TimeoutError:
                print(f"[T] 超时: {unquote(url[-50:])}")
            except Exception as e:
                print(f"[✗] 下载失败: {unquote(url[-40:])} - {str(e)[:30]}")
            
            return False
    
    async def crawl(self, max_files: int = 1000, concurrency: int = 3):
        """开始爬取下载"""
        if not await self.engine.check_connection():
            print("[!] Tor 未连接")
            return
        
        print(f"\n[*] 文件下载器启动")
        print(f"[*] 目标: {self.base_url}")
        print(f"[*] 输出: {self.output_dir}")
        print(f"[*] 文件类型: {', '.join(sorted(self.file_types))}")
        print(f"[*] 最大文件数: {max_files}\n")
        
        # 先扫描目录结构
        self.dir_queue = [self.base_url]
        
        while self.dir_queue:
            url = self.dir_queue.pop(0)
            dirs, files = await self.scan_directory(url)
            
            # 添加新目录
            for d in dirs:
                if d not in self.visited_dirs:
                    self.dir_queue.append(d)
            
            # 添加文件
            for f in files:
                if f not in self.downloaded_files:
                    self.file_queue.append(f)
        
        print(f"\n[*] 扫描完成，发现 {len(self.file_queue)} 个文件待下载\n")
        
        if not self.file_queue:
            print("[!] 没有找到文件")
            await self.engine.close()
            return
        
        # 下载文件
        semaphore = asyncio.Semaphore(concurrency)
        
        while self.file_queue and self.file_count < max_files:
            batch_size = min(concurrency * 2, max_files - self.file_count, len(self.file_queue))
            batch = [self.file_queue.pop(0) for _ in range(batch_size) if self.file_queue]
            
            tasks = [self.download_file(url, semaphore) for url in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            if self.file_count % 10 == 0 and self.file_count > 0:
                size_mb = self.total_size / 1024 / 1024
                print(f"\n[*] 进度: {self.file_count} 文件, {size_mb:.1f} MB, 剩余: {len(self.file_queue)}\n")
        
        await self.engine.close()
        
        size_mb = self.total_size / 1024 / 1024
        print(f"\n[✓] 下载完成!")
        print(f"    文件数: {self.file_count}")
        print(f"    总大小: {size_mb:.1f} MB")
        print(f"    输出目录: {self.output_dir}")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nDEF CON 媒体服务器示例:")
        print("  python file_downloader.py 'http://m6rqq6kocsyugo2laitup5nn32bwm3lh677chuodjfmggczoafzwfcad.onion/DEF%20CON%201/' pdf")
        return
    
    target_url = sys.argv[1]
    
    # 文件类型过滤
    file_types = None
    if len(sys.argv) > 2:
        file_types = set(sys.argv[2].lower().split(','))
    
    downloader = FileDownloader(target_url, file_types=file_types)
    await downloader.crawl(max_files=1000, concurrency=3)


if __name__ == "__main__":
    asyncio.run(main())
