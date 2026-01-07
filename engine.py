#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
洋葱网络引擎 - Tor 控制核心（异步版）
使用 aiohttp + aiohttp-socks 实现高性能异步请求
"""
import json
import asyncio
import aiohttp
from aiohttp_socks import ProxyConnector
from stem import Signal
from stem.control import Controller
import time


class OnionEngine:
    """洋葱网络引擎（异步版）"""
    
    def __init__(self, socks_port=9050, control_port=9051, password=None):
        """
        初始化引擎
        
        Args:
            socks_port: Tor SOCKS5 代理端口（默认 9050）
            control_port: Tor 控制端口（默认 9051）
            password: 控制端口密码（如果设置了的话）
        """
        self.socks_port = socks_port
        self.control_port = control_port
        self.password = password
        self.proxy_url = f'socks5://127.0.0.1:{socks_port}'
        
        # 默认请求头
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        
        # 会话（延迟初始化）
        self._session = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp 会话"""
        if self._session is None or self._session.closed:
            connector = ProxyConnector.from_url(self.proxy_url, rdns=True)
            self._session = aiohttp.ClientSession(
                connector=connector,
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=60)
            )
        return self._session
    
    async def close(self):
        """关闭会话"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def check_connection(self) -> bool:
        """检查 Tor 连接是否正常"""
        try:
            session = await self._get_session()
            async with session.get('https://check.torproject.org/api/ip', timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json()
                if data.get('IsTor', False):
                    print(f"[✓] Tor 连接正常，出口IP: {data.get('IP')}")
                    return True
                else:
                    print("[✗] 连接未通过 Tor 网络")
                    return False
        except Exception as e:
            print(f"[✗] Tor 连接失败: {e}")
            return False
    
    async def get_current_ip(self) -> str:
        """获取当前出口 IP"""
        try:
            session = await self._get_session()
            async with session.get('https://api.ipify.org', timeout=aiohttp.ClientTimeout(total=30)) as resp:
                return (await resp.text()).strip()
        except:
            return "unknown"
    
    def renew_identity(self):
        """
        切换身份（换 IP）
        向 Tor 发送 NEWNYM 信号，构建全新的链路
        注意：这是同步方法，因为 stem 库不支持异步
        """
        try:
            with Controller.from_port(port=self.control_port) as controller:
                if self.password:
                    controller.authenticate(password=self.password)
                else:
                    controller.authenticate()
                
                controller.signal(Signal.NEWNYM)
                
                # 等待新链路构建完成
                wait_time = controller.get_newnym_wait()
                print(f"[*] 正在切换身份，等待 {wait_time:.1f} 秒...")
                time.sleep(wait_time)
                
                print(f"[✓] 身份已切换")
                
        except Exception as e:
            print(f"[✗] 身份切换失败: {e}")
    
    async def fetch(self, url: str, timeout: int = 60) -> tuple:
        """
        获取页面内容
        
        Args:
            url: 目标 URL（支持 .onion）
            timeout: 超时时间（洋葱网络较慢，建议 60 秒）
        
        Returns:
            (status_code, content_type, body_bytes)
        """
        session = await self._get_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            body = await resp.read()
            content_type = resp.headers.get('Content-Type', '')
            return resp.status, content_type, body
    
    async def head(self, url: str, timeout: int = 30) -> dict:
        """
        只获取 Header（用于嗅探资源类型）
        
        Returns:
            {'status': int, 'headers': dict}
        """
        session = await self._get_session()
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as resp:
            return {
                'status': resp.status,
                'headers': dict(resp.headers)
            }


# 同步包装器（兼容旧代码）
class OnionEngineSync:
    """同步版本包装器"""
    
    def __init__(self, *args, **kwargs):
        self._async_engine = OnionEngine(*args, **kwargs)
        self._loop = None
    
    def _get_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop
    
    def check_connection(self) -> bool:
        return self._get_loop().run_until_complete(self._async_engine.check_connection())
    
    def get_current_ip(self) -> str:
        return self._get_loop().run_until_complete(self._async_engine.get_current_ip())
    
    def renew_identity(self):
        self._async_engine.renew_identity()
    
    def fetch(self, url: str, timeout: int = 60):
        return self._get_loop().run_until_complete(self._async_engine.fetch(url, timeout))
    
    def head(self, url: str, timeout: int = 30):
        return self._get_loop().run_until_complete(self._async_engine.head(url, timeout))
    
    def close(self):
        if self._loop and not self._loop.is_closed():
            self._loop.run_until_complete(self._async_engine.close())
            self._loop.close()


if __name__ == "__main__":
    # 测试连接
    async def test():
        engine = OnionEngine()
        
        if await engine.check_connection():
            print(f"当前 IP: {await engine.get_current_ip()}")
        
        await engine.close()
    
    asyncio.run(test())
