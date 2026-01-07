#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深度爬虫 - 全站爬取指定 .onion 站点

用法：
    python deep_crawler.py <目标URL> [最大页数]

示例：
    python deep_crawler.py http://libraryqxxiqakubqv3dc2bend2koqsndbwox2johfywcatxie26bsad.onion/ 500
"""
import re
import os
import asyncio
import hashlib
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from engine import OnionEngine

# 过滤 BeautifulSoup 警告
import warnings
from bs4 import XMLParsedAsHTMLWarning
warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)


class DeepCrawler:
    """全站深度爬虫"""
    
    def __init__(self, base_url: str, output_dir: str = None):
        self.base_url = base_url.rstrip('/')
        self.base_domain = urlparse(base_url).netloc
        self.engine = OnionEngine()
        
        # 输出目录
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            # 用域名前 16 位作为目录名
            self.output_dir = Path(f"crawled-{self.base_domain[:16]}")
        self.output_dir.mkdir(exist_ok=True)
        
        # 状态
        self.visited = set()
        self.queue = []
        self.saved_count = 0
        
        # 锁
        self._lock = asyncio.Lock()
    
    def _is_same_site(self, url: str) -> bool:
        """检查是否同站点"""
        return urlparse(url).netloc == self.base_domain
    
    def _url_to_filename(self, url: str) -> str:
        """URL 转文件名"""
        parsed = urlparse(url)
        path = parsed.path.strip('/') or 'index'
        # 清理非法字符
        path = re.sub(r'[<>:"/\\|?*]', '_', path)
        if parsed.query:
            path += '_' + hashlib.md5(parsed.query.encode()).hexdigest()[:8]
        if not path.endswith('.html'):
            path += '.html'
        return path
    
    def _extract_links(self, html: str, base_url: str) -> set:
        """提取同站点链接"""
        links = set()
        try:
            soup = BeautifulSoup(html, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if href.startswith(('javascript:', 'mailto:', '#')):
                    continue
                full_url = urljoin(base_url, href)
                # 只保留同站点链接
                if self._is_same_site(full_url):
                    # 去掉锚点
                    full_url = full_url.split('#')[0]
                    links.add(full_url)
        except:
            pass
        return links
    
    def _extract_title(self, html: str) -> str:
        """提取标题"""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            if soup.title and soup.title.string:
                return soup.title.string.strip()[:100]
        except:
            pass
        return "Untitled"
    
    async def _save_page(self, url: str, html: str, title: str):
        """保存页面到本地"""
        filename = self._url_to_filename(url)
        filepath = self.output_dir / filename
        
        # 创建子目录
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        # 保存 HTML
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"<!-- URL: {url} -->\n")
            f.write(f"<!-- Title: {title} -->\n")
            f.write(html)
        
        self.saved_count += 1
    
    async def crawl_page(self, url: str, semaphore: asyncio.Semaphore) -> set:
        """爬取单个页面"""
        async with semaphore:
            if url in self.visited:
                return set()
            
            new_links = set()
            
            try:
                status, content_type, body = await self.engine.fetch(url, timeout=60)
                
                if 'text/html' not in content_type and content_type:
                    # 非 HTML，跳过
                    return new_links
                
                html = body.decode('utf-8', errors='ignore')
                title = self._extract_title(html)
                
                # 保存
                await self._save_page(url, html, title)
                
                async with self._lock:
                    self.visited.add(url)
                
                print(f"[+] {title[:40]}... @ {url[-50:]}")
                
                # 提取链接
                new_links = self._extract_links(html, url)
                
            except asyncio.TimeoutError:
                print(f"[T] 超时: {url[-50:]}")
            except Exception as e:
                err = str(e)[:30]
                if 'connect to proxy' not in err.lower():
                    print(f"[✗] {url[-40:]} - {err}")
            
            return new_links - self.visited
    
    async def crawl(self, max_pages: int = 500, concurrency: int = 5):
        """开始爬取"""
        # 检查连接
        if not await self.engine.check_connection():
            print("[!] Tor 未连接")
            return
        
        self.queue = [self.base_url]
        semaphore = asyncio.Semaphore(concurrency)
        
        print(f"\n[*] 深度爬取: {self.base_url}")
        print(f"[*] 输出目录: {self.output_dir}")
        print(f"[*] 最大页数: {max_pages}, 并发: {concurrency}\n")
        
        while self.queue and self.saved_count < max_pages:
            # 取一批
            batch_size = min(concurrency * 2, max_pages - self.saved_count, len(self.queue))
            batch = []
            while self.queue and len(batch) < batch_size:
                url = self.queue.pop(0)
                if url not in self.visited:
                    batch.append(url)
            
            if not batch:
                break
            
            # 并发爬取
            tasks = [self.crawl_page(url, semaphore) for url in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, set):
                    self.queue.extend(result)
            
            # 进度
            if self.saved_count % 20 == 0 and self.saved_count > 0:
                print(f"\n[*] 进度: {self.saved_count} 页, 队列: {len(self.queue)}\n")
        
        await self.engine.close()
        
        print(f"\n[✓] 爬取完成!")
        print(f"    保存页面: {self.saved_count}")
        print(f"    输出目录: {self.output_dir}")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n已知高价值站点:")
        print("  - http://libraryqxxiqakubqv3dc2bend2koqsndbwox2johfywcatxie26bsad.onion/  (无政府主义图书馆)")
        print("  - http://nv3x2jozywh63fkohn5mwp2d73vasusjixn3im3ueof52fmbjsigw6ad.onion/  (漫画图书馆)")
        return
    
    target_url = sys.argv[1]
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    
    crawler = DeepCrawler(target_url)
    await crawler.crawl(max_pages=max_pages, concurrency=5)


if __name__ == "__main__":
    asyncio.run(main())
