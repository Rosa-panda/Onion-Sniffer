#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置文件示例 - 复制为 config.py 并填入真实配置

使用方法：
    cp config.example.py config.py
    # 编辑 config.py 填入你的配置
"""

# PostgreSQL 数据库配置
PG_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'onion_data',
    'user': 'postgres',
    'password': 'your_password_here'  # 修改为你的密码
}

# Tor 代理配置
TOR_CONFIG = {
    'socks_port': 9050,      # SOCKS5 代理端口
    'control_port': 9051,    # 控制端口（用于切换身份）
    'control_password': None  # 控制端口密码（如果设置了的话）
}
