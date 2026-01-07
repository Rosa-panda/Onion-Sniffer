# 🧅 Onion Sniffer - 洋葱嗅探器

高性能异步 .onion 站点爬虫，用于 Tor 网络数据挖掘和威胁情报收集。

## ✨ 特性

- **异步高并发**：基于 aiohttp + aiohttp-socks，支持 10+ 并发连接
- **智能去重**：SimHash 相似度检测，自动跳过镜像站和相似页面
- **PostgreSQL 后端**：支持百万级数据存储，适合长期运行
- **自动过滤**：清网镜像站黑名单、垃圾域名检测、失败域名自动拉黑
- **断点续爬**：支持中断后继续，无限爬取模式
- **资源发现**：自动识别 PDF、ZIP 等文档资源

## 📦 组件

| 文件 | 功能 |
|------|------|
| `engine.py` | Tor 网络引擎，封装 aiohttp + SOCKS5 代理 |
| `sniffer.py` | 主爬虫，自动发现和爬取 .onion 站点 |
| `deep_crawler.py` | 深度爬虫，全站爬取指定站点 |
| `file_downloader.py` | 文件下载器，支持目录索引站批量下载 |
| `export_data.py` | 数据导出工具，支持 CSV/JSON/SQL 格式 |

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install aiohttp aiohttp-socks asyncpg beautifulsoup4 stem
```

### 2. 配置 Tor 代理

**方式一：本地 Tor**
```bash
# Linux/Mac
sudo apt install tor && sudo systemctl start tor

# Windows
# 下载 Tor Expert Bundle，运行 tor.exe
```

**方式二：SSH 隧道（推荐，适合网络受限环境）**
```bash
# 在 VPS 上安装 Tor
sudo apt install tor -y && sudo systemctl start tor

# 本地建立隧道
ssh -L 9050:127.0.0.1:9050 -N user@your-vps
```

### 3. 配置

```bash
# 复制示例配置
cp config.example.py config.py

# 编辑 config.py，填入你的数据库密码
```

config.py 示例：
```python
PG_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'onion_data',
    'user': 'postgres',
    'password': 'your_password'
}
```

### 4. 开始爬取

```bash
# 初始爬取（使用内置种子）
python sniffer.py

# 继续爬取（使用已发现的站点作为种子）
python sniffer.py --continue

# 指定最大页数
python sniffer.py 1000
```

## 🔧 高级用法

### 深度爬取指定站点

```bash
python deep_crawler.py "http://xxx.onion/" 500
```

### 下载文件（支持目录索引站）

```bash
# 下载所有文件
python file_downloader.py "http://xxx.onion/files/"

# 只下载 PDF
python file_downloader.py "http://xxx.onion/files/" pdf

# 下载多种类型
python file_downloader.py "http://xxx.onion/files/" pdf,zip,mp4
```

### 导出数据

```bash
# 查看统计
python export_data.py stats

# 导出为 CSV（生成 3 个文件：站点、页面、文档）
python export_data.py csv onion_data

# 导出为 JSON
python export_data.py json onion_data.json

# 导出为 SQL
python export_data.py sql onion_data.sql
```

## 📊 数据库结构

```sql
-- 发现的站点
onion_sites (domain, first_seen, last_seen, status, priority)

-- 爬取的页面
pages (url, domain, title, content_hash, simhash, relevance_score, crawled_at)

-- 发现的文档资源
documents (url, filename, content_type, size, downloaded_at)
```

### 常用查询

```sql
-- 高价值页面
SELECT title, url, relevance_score FROM pages 
WHERE relevance_score > 0.5 ORDER BY relevance_score DESC;

-- 按域名统计
SELECT domain, COUNT(*) as pages FROM pages 
GROUP BY domain ORDER BY pages DESC LIMIT 20;

-- 发现的文档
SELECT url, content_type FROM documents;
```

## 🛡️ 过滤机制

### 清网镜像站黑名单
自动跳过 Wikipedia、Facebook、DuckDuckGo 等清网站点的洋葱镜像。

### 垃圾域名检测
过滤连续重复字符的无效域名（如 `aaaaaaa...onion`）。

### 失败域名自动拉黑
连续失败 3 次的域名自动加入黑名单，避免浪费资源。

## ⚠️ 免责声明

本工具仅供安全研究和威胁情报收集使用。使用者需遵守当地法律法规，对使用本工具产生的任何后果自行负责。

## 📄 License

MIT
