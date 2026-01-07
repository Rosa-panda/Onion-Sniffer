#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
洋葱嗅探器 - 自动发现和爬取 .onion 站点（异步版 + PostgreSQL）

优化点：
1. aiohttp 异步请求，大幅提升并发性能
2. SimHash 相似度检测（防止爬取镜像站/相似页面）
3. 关键词过滤（只保留技术相关内容）
4. PostgreSQL 后端（支持超大数据量）
5. 无限爬取模式（max_pages=0）
"""
import re
import hashlib
import asyncio
import warnings
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from collections import Counter
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from engine import OnionEngine

# 过滤 BeautifulSoup 的 XML 警告
warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)

# 从配置文件加载
try:
    from config import PG_CONFIG
except ImportError:
    # 默认配置（需要创建 config.py）
    PG_CONFIG = {
        'host': 'localhost',
        'port': 5432,
        'database': 'onion_data',
        'user': 'postgres',
        'password': 'your_password_here'
    }
    print("[!] 警告: 未找到 config.py，请复制 config.example.py 并配置")

# V3 洋葱地址正则（56 位 base32 字符）
ONION_PATTERN = re.compile(r'[a-z2-7]{56}\.onion', re.IGNORECASE)

# 清网镜像站黑名单（这些站点的洋葱版本没有独特内容）
CLEARNET_MIRRORS = {
    'wikipedia', 'wikimedia', 'wikidata', 'wikiquote', 'wikisource',
    'facebook', 'facebookcorewwwi', 'facebookwkhpilnemxj7asaniu7vnjjbiltxjqhye3m',
    'twitter', 'nytimes', 'bbc', 'cnn', 'theguardian',
    'protonmail', 'proton', 'tutanota',
    'duckduckgo', 'startpage', 'searx',
    'debian', 'ubuntu', 'archlinux', 'gentoo',
    'torproject', 'tails', 'whonix',
    'fsfe', 'eff', 'aclu',
    'ciadotgov', 'cia',
    'securedrop',  # 虽然有价值但都是清网媒体的入口
    'anarchistlibrary', 'theanarchistlibrary',
    'goodgame',  # 游戏服务器托管
    'flibusta',  # 俄语电子书（清网有）
}

# 技术相关关键词（用于过滤垃圾内容）
TECH_KEYWORDS = {
    'security', 'hacking', 'exploit', 'vulnerability', 'malware', 'reverse',
    'programming', 'code', 'linux', 'windows', 'kernel', 'binary', 'ctf',
    'crypto', 'encryption', 'forensic', 'pentest', 'research', 'tool',
    'github', 'git', 'python', 'rust', 'assembly', 'debug', 'analysis',
    'leak', 'database', 'dump', 'source', 'documentation', 'tutorial',
    'forum', 'community', 'wiki', 'library', 'archive', 'mirror',
}


class SimHash:
    """SimHash 相似度检测"""
    def __init__(self, hash_bits=64):
        self.hash_bits = hash_bits
    
    def _tokenize(self, text: str) -> list:
        return re.findall(r'\w+', text.lower())
    
    def compute(self, text: str) -> int:
        tokens = self._tokenize(text)
        if not tokens:
            return 0
        
        token_counts = Counter(tokens)
        v = [0] * self.hash_bits
        
        for token, count in token_counts.items():
            token_hash = int(hashlib.md5(token.encode()).hexdigest(), 16)
            for i in range(self.hash_bits):
                bit = (token_hash >> i) & 1
                if bit:
                    v[i] += count
                else:
                    v[i] -= count
        
        fingerprint = 0
        for i in range(self.hash_bits):
            if v[i] > 0:
                fingerprint |= (1 << i)
        return fingerprint
    
    def distance(self, hash1: int, hash2: int) -> int:
        return bin(hash1 ^ hash2).count('1')
    
    def is_similar(self, hash1: int, hash2: int, threshold: int = 10) -> bool:
        return self.distance(hash1, hash2) <= threshold


class OnionSniffer:
    """洋葱网络嗅探器（异步版 + PostgreSQL）"""
    
    def __init__(self, pg_config: dict = None):
        self.engine = OnionEngine()
        self.pg_config = pg_config or PG_CONFIG
        self.simhash = SimHash()
        
        # 内存中的已访问集合
        self.visited_urls = set()
        self.visited_hashes = set()
        self.simhashes = []
        
        # 域名失败计数器（连续失败 N 次就跳过）
        self.domain_fail_count = {}
        self.blacklisted_domains = set()
        self.MAX_DOMAIN_FAILS = 3  # 连续失败 3 次就拉黑
        
        # 异步锁（保护共享状态）
        self._lock = asyncio.Lock()
        
        # 数据库连接池（延迟初始化）
        self._pool = None
    
    async def _get_pool(self):
        """获取或创建 PostgreSQL 连接池"""
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                host=self.pg_config['host'],
                port=self.pg_config['port'],
                database=self.pg_config['database'],
                user=self.pg_config['user'],
                password=self.pg_config['password'],
                min_size=2,
                max_size=10
            )
        return self._pool
    
    async def init_db(self):
        """初始化 PostgreSQL 数据库"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # 创建表
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS onion_sites (
                    id SERIAL PRIMARY KEY,
                    domain TEXT UNIQUE,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP,
                    status TEXT DEFAULT 'pending',
                    priority INTEGER DEFAULT 0
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS pages (
                    id SERIAL PRIMARY KEY,
                    url TEXT UNIQUE,
                    domain TEXT,
                    title TEXT,
                    content_hash TEXT,
                    simhash TEXT,
                    content_type TEXT,
                    relevance_score REAL DEFAULT 0,
                    crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    url TEXT UNIQUE,
                    filename TEXT,
                    content_type TEXT,
                    size INTEGER,
                    local_path TEXT,
                    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 创建索引（加速查询）
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_pages_domain ON pages(domain)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_pages_relevance ON pages(relevance_score)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_pages_content_hash ON pages(content_hash)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_sites_domain ON onion_sites(domain)')
        
        await self._load_visited()
    
    async def _load_visited(self):
        """从数据库加载已访问的 URL"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # 加载已访问 URL
            rows = await conn.fetch("SELECT url FROM pages")
            self.visited_urls = {row['url'] for row in rows}
            
            # 加载内容哈希
            rows = await conn.fetch("SELECT content_hash FROM pages WHERE content_hash IS NOT NULL")
            self.visited_hashes = {row['content_hash'] for row in rows}
            
            # 加载 SimHash（字符串转整数，处理科学计数法）
            rows = await conn.fetch("SELECT simhash FROM pages WHERE simhash IS NOT NULL")
            self.simhashes = []
            for row in rows:
                if row['simhash']:
                    try:
                        # 处理科学计数法格式
                        self.simhashes.append(int(float(row['simhash'])))
                    except:
                        pass
        
        print(f"[*] 已加载 {len(self.visited_urls)} 个已访问 URL，{len(self.simhashes)} 个 SimHash")
    
    def _is_content_similar(self, simhash: int) -> bool:
        for existing_hash in self.simhashes:
            if self.simhash.is_similar(simhash, existing_hash, threshold=8):
                return True
        return False
    
    async def _record_domain_fail(self, domain: str):
        """记录域名失败，连续失败超过阈值就拉黑"""
        async with self._lock:
            self.domain_fail_count[domain] = self.domain_fail_count.get(domain, 0) + 1
            if self.domain_fail_count[domain] >= self.MAX_DOMAIN_FAILS:
                self.blacklisted_domains.add(domain)
                print(f"[🚫] 域名拉黑（连续失败 {self.MAX_DOMAIN_FAILS} 次）: {domain[:40]}")
    
    def _calculate_relevance(self, text: str, title: str) -> float:
        text_lower = (text + " " + title).lower()
        matches = sum(1 for kw in TECH_KEYWORDS if kw in text_lower)
        return min(matches / 5.0, 1.0)
    
    def extract_onion_links(self, html: str, base_url: str) -> set:
        links = set()
        
        for match in ONION_PATTERN.finditer(html):
            domain = match.group(0).lower()
            # 过滤垃圾地址（连续重复字符太多的）
            if self._is_junk_domain(domain):
                continue
            # 过滤清网镜像站
            if self._is_clearnet_mirror(domain):
                continue
            links.add(f"http://{domain}/")
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if href.startswith(('gopher://', 'irc://', 'mailto:', 'javascript:', 'ftp://', 'magnet:')):
                    continue
                full_url = urljoin(base_url, href)
                if '.onion' in full_url and full_url.startswith(('http://', 'https://')):
                    domain = urlparse(full_url).netloc
                    # 过滤垃圾域名和清网镜像
                    if self._is_junk_domain(domain) or self._is_clearnet_mirror(domain):
                        continue
                    links.add(full_url)
        except:
            pass
        
        return links
    
    def _is_junk_domain(self, domain: str) -> bool:
        """检测垃圾域名（连续重复字符太多）"""
        # 提取 .onion 前的部分
        name = domain.replace('.onion', '')
        if len(name) < 10:
            return True
        # 检查是否有连续 10 个以上相同字符
        for i in range(len(name) - 9):
            if len(set(name[i:i+10])) == 1:
                return True
        return False
    
    def _is_clearnet_mirror(self, domain: str) -> bool:
        """检测清网镜像站"""
        domain_lower = domain.lower()
        for mirror in CLEARNET_MIRRORS:
            if mirror in domain_lower:
                return True
        return False
    
    async def _save_page(self, data: dict):
        """保存页面到数据库"""
        # 只保存 .onion 域名
        if not data['domain'].endswith('.onion'):
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                # SimHash 转字符串存储
                simhash_str = str(data['simhash']) if data.get('simhash') else None
                await conn.execute('''
                    INSERT INTO pages (url, domain, title, content_hash, simhash, content_type, relevance_score)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (url) DO UPDATE SET
                        title = EXCLUDED.title,
                        content_hash = EXCLUDED.content_hash,
                        simhash = EXCLUDED.simhash,
                        relevance_score = EXCLUDED.relevance_score,
                        crawled_at = CURRENT_TIMESTAMP
                ''', data['url'], data['domain'], data['title'], data['content_hash'],
                    simhash_str, data['content_type'], data['relevance'])
            except Exception as e:
                print(f"[!] DB 错误 (page): {e}")
    
    async def _save_site(self, domain: str):
        """保存站点到数据库"""
        # 只保存 .onion 域名
        if not domain.endswith('.onion'):
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute('''
                    INSERT INTO onion_sites (domain) VALUES ($1)
                    ON CONFLICT (domain) DO NOTHING
                ''', domain)
            except Exception as e:
                print(f"[!] DB 错误 (site): {e}")
    
    async def _save_document(self, url: str, content_type: str):
        """保存文档到数据库"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute('''
                    INSERT INTO documents (url, content_type) VALUES ($1, $2)
                    ON CONFLICT (url) DO NOTHING
                ''', url, content_type)
            except Exception as e:
                print(f"[!] DB 错误 (doc): {e}")
    
    def _extract_title(self, html: str) -> str:
        try:
            soup = BeautifulSoup(html, 'html.parser')
            if soup.title and soup.title.string:
                return soup.title.string.strip()[:100]
        except:
            pass
        return "Untitled"
    
    async def sniff_page(self, url: str, semaphore: asyncio.Semaphore) -> set:
        """异步嗅探单个页面"""
        async with semaphore:
            # 快速检查是否已访问
            if url in self.visited_urls:
                return set()
            
            # 检查域名是否被拉黑
            domain = urlparse(url).netloc
            if domain in self.blacklisted_domains:
                return set()
            
            # 跳过清网镜像站
            if self._is_clearnet_mirror(domain):
                return set()
            
            new_links = set()
            
            try:
                # 1. HEAD 请求探测类型
                head = await self.engine.head(url, timeout=30)
                content_type = head['headers'].get('Content-Type', '')
                
                # 2. 文档资源
                if any(ext in content_type.lower() for ext in ['pdf', 'zip', 'rar', 'octet-stream']):
                    print(f"[!] 发现资源: {content_type[:20]} @ {url[:50]}")
                    await self._save_document(url, content_type)
                    return new_links
                
                # 3. HTML 页面
                if 'text/html' in content_type or not content_type:
                    status, ct, body = await self.engine.fetch(url, timeout=60)
                    html = body.decode('utf-8', errors='ignore')
                    
                    # MD5 去重
                    content_hash = hashlib.md5(html.encode()).hexdigest()
                    if content_hash in self.visited_hashes:
                        return new_links
                    
                    # SimHash 相似度去重
                    simhash_val = self.simhash.compute(html)
                    if self._is_content_similar(simhash_val):
                        return new_links
                    
                    title = self._extract_title(html)
                    relevance = self._calculate_relevance(html, title)
                    domain = urlparse(url).netloc
                    
                    # 保存
                    await self._save_page({
                        'url': url, 'domain': domain, 'title': title,
                        'content_hash': content_hash, 'simhash': simhash_val,
                        'content_type': content_type, 'relevance': relevance
                    })
                    
                    # 保存站点
                    await self._save_site(domain)
                    
                    # 更新内存状态
                    async with self._lock:
                        self.visited_urls.add(url)
                        self.visited_hashes.add(content_hash)
                        self.simhashes.append(simhash_val)
                    
                    marker = "★" if relevance > 0.5 else "+"
                    print(f"[{marker}] {title[:35]}... (r:{relevance:.1f}) @ {url[:45]}")
                    
                    # 提取链接
                    new_links = self.extract_onion_links(html, url)
                    for link in new_links:
                        d = urlparse(link).netloc
                        if d:
                            await self._save_site(d)
                    
                    # 成功了，重置该域名的失败计数
                    if domain in self.domain_fail_count:
                        del self.domain_fail_count[domain]
            
            except asyncio.TimeoutError:
                print(f"[T] 超时: {url[:50]}")
                await self._record_domain_fail(domain)
            except Exception as e:
                err_msg = str(e)
                short_msg = err_msg[:40]
                
                # 代理连接失败不计入域名失败（是网络问题，不是站点问题）
                if 'connect to proxy' in err_msg.lower() or 'Errno 22' in err_msg:
                    print(f"[⚠] 代理断开: {short_msg}")
                    # 不记录域名失败
                elif 'Cannot connect' not in short_msg and 'Connection refused' not in short_msg:
                    print(f"[✗] {url[:40]}... - {short_msg}")
                    await self._record_domain_fail(domain)
            
            return new_links - self.visited_urls
    
    async def crawl_async(self, seeds: list, max_pages: int = 100, concurrency: int = 10):
        """异步爬取主循环，max_pages=0 表示无限爬取"""
        # 初始化数据库
        await self.init_db()
        
        # 检查连接
        if not await self.engine.check_connection():
            print("[!] Tor 未连接")
            return
        
        queue = list(seeds)
        crawled = 0
        semaphore = asyncio.Semaphore(concurrency)
        unlimited = (max_pages == 0)
        
        target_str = "∞" if unlimited else str(max_pages)
        print(f"\n[*] 异步爬取启动，种子: {len(seeds)}，目标: {target_str}，并发: {concurrency}\n")
        
        while queue and (unlimited or crawled < max_pages):
            # 取一批 URL
            if unlimited:
                batch_size = min(concurrency * 2, len(queue))
            else:
                batch_size = min(concurrency * 2, max_pages - crawled, len(queue))
            
            batch = []
            while queue and len(batch) < batch_size:
                url = queue.pop(0)
                domain = urlparse(url).netloc
                # 跳过已访问和黑名单域名
                if url not in self.visited_urls and domain not in self.blacklisted_domains:
                    batch.append(url)
            
            if not batch:
                if not queue:
                    # 队列空了，尝试从数据库补充
                    new_seeds = await self._get_pending_seeds_async(limit=50)
                    if new_seeds:
                        queue.extend(new_seeds)
                        print(f"[*] 队列空，从数据库补充 {len(new_seeds)} 个种子")
                        continue
                    else:
                        break
                continue
            
            # 并发执行
            tasks = [self.sniff_page(url, semaphore) for url in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, set):
                    queue.extend(result)
                    crawled += 1
            
            # 定期打印进度
            if crawled % 50 == 0 and crawled > 0:
                print(f"\n[*] 进度: 已爬取 {crawled} 页，队列: {len(queue)}\n")
        
        await self.engine.close()
        print(f"\n[✓] 爬取完成，共 {crawled} 页")
        
        # 先打印统计，再关闭连接池
        await self._print_stats_async()
        
        if self._pool:
            await self._pool.close()
    
    async def _get_pending_seeds_async(self, limit: int = 50) -> list:
        """从数据库获取待爬取的站点作为种子"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT DISTINCT 'http://' || s.domain || '/' as url
                FROM onion_sites s
                WHERE NOT EXISTS (
                    SELECT 1 FROM pages p WHERE p.domain = s.domain
                )
                LIMIT $1
            ''', limit)
            return [row['url'] for row in rows]
    
    async def _get_high_relevance_seeds_async(self, min_score: float = 0.5, limit: int = 20) -> list:
        """获取高相关度页面的同域名其他页面作为种子"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT domain, MAX(relevance_score) as max_score
                FROM pages 
                WHERE relevance_score >= $1 
                GROUP BY domain
                ORDER BY max_score DESC
                LIMIT $2
            ''', min_score, limit)
            return [f"http://{row['domain']}/" for row in rows]
    
    async def _print_stats_async(self):
        """打印统计信息"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            sites = await conn.fetchval("SELECT COUNT(*) FROM onion_sites")
            pages = await conn.fetchval("SELECT COUNT(*) FROM pages")
            docs = await conn.fetchval("SELECT COUNT(*) FROM documents")
            relevant = await conn.fetchval("SELECT COUNT(*) FROM pages WHERE relevance_score > 0.5")
            top_pages = await conn.fetch('''
                SELECT title, url, relevance_score 
                FROM pages 
                ORDER BY relevance_score DESC 
                LIMIT 5
            ''')
        
        print(f"\n{'='*50}")
        print(f"📊 爬取统计")
        print(f"{'='*50}")
        print(f"发现站点: {sites}")
        print(f"爬取页面: {pages}")
        print(f"发现文档: {docs}")
        print(f"高相关页: {relevant}")
        
        if top_pages:
            print(f"\n🔥 最相关的页面:")
            for row in top_pages:
                t = row['title'][:35] if row['title'] else "Untitled"
                print(f"  [{row['relevance_score']:.1f}] {t} - {row['url'][:45]}")
    
    def crawl(self, seeds: list, max_pages: int = 100, concurrency: int = 10):
        """同步入口"""
        asyncio.run(self.crawl_async(seeds, max_pages, concurrency))
    
    def crawl_continue(self, max_pages: int = 0, concurrency: int = 10):
        """继续爬取：使用已发现的站点作为种子，max_pages=0 表示无限"""
        asyncio.run(self._crawl_continue_async(max_pages, concurrency))
    
    async def _crawl_continue_async(self, max_pages: int = 0, concurrency: int = 10):
        """继续爬取的异步实现"""
        await self.init_db()
        
        # 优先爬高相关度站点
        seeds = await self._get_high_relevance_seeds_async(min_score=0.5, limit=50)
        # 补充未爬取的站点（取更多）
        pending = await self._get_pending_seeds_async(limit=500)
        seeds.extend([s for s in pending if s not in seeds])
        
        if not seeds:
            print("[!] 没有待爬取的站点，请先运行初始爬取")
            return
        
        print(f"[*] 从数据库加载 {len(seeds)} 个种子站点")
        await self.crawl_async(seeds, max_pages, concurrency)


if __name__ == "__main__":
    import sys
    
    sniffer = OnionSniffer()
    
    # 支持命令行参数
    if len(sys.argv) > 1 and sys.argv[1] == '--continue':
        # 继续爬取模式：使用已发现的站点，默认无限爬取
        max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        sniffer.crawl_continue(max_pages=max_pages, concurrency=10)
    else:
        # 初始爬取模式
        seeds = [
            "http://zqktlwiuavvvqqt4ybvgvi7tyo4hjl5xgfuvpdf6otjiycgwqbym2qad.onion/wiki/index.php/Main_Page",
            "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/",
            "http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/",
            "http://xmh57jrknzkhv6y3ls3ubitzfqnkrwxhopf5aygthi7d6rplyvk3noyd.onion/",
            "http://p53lf57qovyuvwsc6xnrppyply3vtqm7l6pcobkmyqsiofyeznfu5uqd.onion/",
        ]
        # 默认无限爬取
        max_pages = int(sys.argv[1]) if len(sys.argv) > 1 else 0
        sniffer.crawl(seeds, max_pages=max_pages, concurrency=10)
