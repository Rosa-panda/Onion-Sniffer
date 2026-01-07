#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据导出工具 - 导出爬取的数据供分享

用法：
    python export_data.py [格式] [输出文件]

格式：
    csv  - CSV 格式（默认）
    json - JSON 格式
    sql  - SQL INSERT 语句
    md   - Markdown 格式（GitHub 可点击链接）

示例：
    python export_data.py csv onion_data.csv
    python export_data.py json onion_data.json
    python export_data.py md SITES.md
"""
import sys
import json
import csv
import asyncio
import asyncpg
from datetime import datetime

# 从配置文件加载
try:
    from config import PG_CONFIG
except ImportError:
    PG_CONFIG = {
        'host': 'localhost',
        'port': 5432,
        'database': 'onion_data',
        'user': 'postgres',
        'password': 'your_password_here'
    }
    print("[!] 警告: 未找到 config.py，请复制 config.example.py 并配置")


async def export_csv(output_file: str):
    """导出为 CSV 格式"""
    conn = await asyncpg.connect(**PG_CONFIG)
    
    # 导出站点
    sites = await conn.fetch("SELECT domain, first_seen, last_seen, status FROM onion_sites ORDER BY first_seen")
    with open(f"{output_file}_sites.csv", 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['domain', 'first_seen', 'last_seen', 'status'])
        for row in sites:
            writer.writerow([row['domain'], row['first_seen'], row['last_seen'], row['status']])
    print(f"[✓] 导出 {len(sites)} 个站点到 {output_file}_sites.csv")
    
    # 导出页面
    pages = await conn.fetch("""
        SELECT url, domain, title, relevance_score, crawled_at 
        FROM pages ORDER BY relevance_score DESC
    """)
    with open(f"{output_file}_pages.csv", 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['url', 'domain', 'title', 'relevance_score', 'crawled_at'])
        for row in pages:
            writer.writerow([row['url'], row['domain'], row['title'], row['relevance_score'], row['crawled_at']])
    print(f"[✓] 导出 {len(pages)} 个页面到 {output_file}_pages.csv")
    
    # 导出文档
    docs = await conn.fetch("SELECT url, content_type, downloaded_at FROM documents")
    with open(f"{output_file}_docs.csv", 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['url', 'content_type', 'downloaded_at'])
        for row in docs:
            writer.writerow([row['url'], row['content_type'], row['downloaded_at']])
    print(f"[✓] 导出 {len(docs)} 个文档到 {output_file}_docs.csv")
    
    await conn.close()


async def export_json(output_file: str):
    """导出为 JSON 格式"""
    conn = await asyncpg.connect(**PG_CONFIG)
    
    sites = await conn.fetch("SELECT domain, first_seen, status FROM onion_sites")
    pages = await conn.fetch("SELECT url, domain, title, relevance_score FROM pages")
    docs = await conn.fetch("SELECT url, content_type FROM documents")
    
    data = {
        'exported_at': datetime.now().isoformat(),
        'stats': {
            'sites': len(sites),
            'pages': len(pages),
            'documents': len(docs)
        },
        'sites': [dict(row) for row in sites],
        'pages': [dict(row) for row in pages],
        'documents': [dict(row) for row in docs]
    }
    
    # 处理 datetime 序列化
    def json_serial(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=json_serial)
    
    print(f"[✓] 导出到 {output_file}")
    print(f"    站点: {len(sites)}")
    print(f"    页面: {len(pages)}")
    print(f"    文档: {len(docs)}")
    
    await conn.close()


async def export_markdown(output_file: str):
    """导出为 Markdown 格式（可在 GitHub 直接点击）"""
    conn = await asyncpg.connect(**PG_CONFIG)
    
    # 按域名分组，取每个域名最高相关度的页面
    pages = await conn.fetch("""
        SELECT DISTINCT ON (domain) 
            domain, title, relevance_score, url
        FROM pages 
        WHERE title IS NOT NULL AND title != 'Untitled'
        ORDER BY domain, relevance_score DESC
    """)
    
    # 统计
    total_sites = await conn.fetchval("SELECT COUNT(DISTINCT domain) FROM pages")
    total_pages = await conn.fetchval("SELECT COUNT(*) FROM pages")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("# 🧅 Onion Sites Collection\n\n")
        f.write(f"嗅探器发现的 .onion 站点列表。\n\n")
        f.write(f"- 独立站点: **{total_sites}**\n")
        f.write(f"- 总页面数: **{total_pages}**\n")
        f.write(f"- 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        
        f.write("## ⚠️ 免责声明\n\n")
        f.write("这些链接仅供安全研究使用。大部分 Tor 隐藏服务是诈骗、市场或垃圾内容。\n")
        f.write("访问需要 Tor 浏览器。\n\n")
        
        f.write("## 站点列表\n\n")
        f.write("| 标题 | 域名 | 相关度 |\n")
        f.write("|------|------|--------|\n")
        
        for row in pages:
            # 清理标题：移除换行、转义管道符、截断
            title = (row['title'] or 'Untitled')
            title = title.replace('\n', ' ').replace('\r', ' ')
            title = title.replace('|', '-')  # 直接替换为横杠，避免转义问题
            title = title.replace('[', '(').replace(']', ')')  # 避免破坏链接语法
            title = ' '.join(title.split())[:60]  # 合并多余空格并截断
            
            domain = row['domain'][:50]
            score = row['relevance_score']
            # 生成可点击的 onion 链接
            link = f"http://{row['domain']}/"
            f.write(f"| {title} | [{domain}]({link}) | {score:.1f} |\n")
    
    print(f"[✓] 导出 {len(pages)} 个站点到 {output_file}")
    await conn.close()


async def export_sql(output_file: str):
    """导出为 SQL INSERT 语句"""
    conn = await asyncpg.connect(**PG_CONFIG)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("-- Onion Sniffer 数据导出\n")
        f.write(f"-- 导出时间: {datetime.now().isoformat()}\n\n")
        
        # 站点
        sites = await conn.fetch("SELECT domain, status FROM onion_sites")
        f.write("-- 站点数据\n")
        for row in sites:
            domain = row['domain'].replace("'", "''")
            f.write(f"INSERT INTO onion_sites (domain, status) VALUES ('{domain}', '{row['status']}') ON CONFLICT DO NOTHING;\n")
        
        # 页面
        pages = await conn.fetch("SELECT url, domain, title, relevance_score FROM pages")
        f.write("\n-- 页面数据\n")
        for row in pages:
            url = row['url'].replace("'", "''")
            domain = row['domain'].replace("'", "''")
            title = (row['title'] or '').replace("'", "''")
            f.write(f"INSERT INTO pages (url, domain, title, relevance_score) VALUES ('{url}', '{domain}', '{title}', {row['relevance_score']}) ON CONFLICT DO NOTHING;\n")
    
    print(f"[✓] 导出 {len(sites)} 站点 + {len(pages)} 页面到 {output_file}")
    
    await conn.close()


async def print_stats():
    """打印统计信息"""
    conn = await asyncpg.connect(**PG_CONFIG)
    
    sites = await conn.fetchval("SELECT COUNT(*) FROM onion_sites")
    pages = await conn.fetchval("SELECT COUNT(*) FROM pages")
    docs = await conn.fetchval("SELECT COUNT(*) FROM documents")
    relevant = await conn.fetchval("SELECT COUNT(*) FROM pages WHERE relevance_score > 0.5")
    
    print(f"\n📊 数据库统计")
    print(f"{'='*40}")
    print(f"站点总数: {sites}")
    print(f"页面总数: {pages}")
    print(f"文档资源: {docs}")
    print(f"高相关页: {relevant}")
    
    # 按域名统计
    top_domains = await conn.fetch("""
        SELECT domain, COUNT(*) as cnt 
        FROM pages GROUP BY domain 
        ORDER BY cnt DESC LIMIT 10
    """)
    print(f"\n🔝 页面最多的站点:")
    for row in top_domains:
        print(f"  {row['cnt']:4d} - {row['domain'][:50]}")
    
    await conn.close()


async def main():
    if len(sys.argv) < 2:
        await print_stats()
        print(__doc__)
        return
    
    fmt = sys.argv[1].lower()
    output = sys.argv[2] if len(sys.argv) > 2 else f"onion_export_{datetime.now().strftime('%Y%m%d')}"
    
    if fmt == 'csv':
        await export_csv(output)
    elif fmt == 'json':
        if not output.endswith('.json'):
            output += '.json'
        await export_json(output)
    elif fmt == 'sql':
        if not output.endswith('.sql'):
            output += '.sql'
        await export_sql(output)
    elif fmt in ('md', 'markdown'):
        if not output.endswith('.md'):
            output += '.md'
        await export_markdown(output)
    elif fmt == 'stats':
        await print_stats()
    else:
        print(f"[!] 未知格式: {fmt}")
        print("支持: csv, json, sql, md, stats")


if __name__ == "__main__":
    asyncio.run(main())
