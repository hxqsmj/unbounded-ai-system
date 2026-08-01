"""
微信 UI 桥接器 — Win32 + 模拟键盘收发消息 (不注入, 不封号)

原理:
  通过 Win32 API 定位微信窗口, 用键盘快捷键模拟操作:
  - Ctrl+Tab 切换聊天
  - 监控新消息 → 推送到 WSS Gateway
  - 接收发送指令 → 模拟键盘打字发送

用法:
  # 启动桥接器
  python scripts/wechat_ui_bridge.py --account sales_01

  # 调试模式 — 打印窗口信息
  python scripts/wechat_ui_bridge.py --debug

前提:
  1. 微信已登录, 窗口可见 (不要最小化到托盘)
  2. WSS Gateway 已启动
  3. 安装依赖: pip install pywin32 pyautogui
"""

import asyncio
import ctypes
import json
import sys
import time
import argparse
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Optional

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Win32 API ────────────────────────────────────────────────
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def find_wechat_window() -> Optional[int]:
    """查找微信主窗口句柄"""
    # WeChat 4.x 类名: Qt51514QWindowIcon
    for cls in ["Qt51514QWindowIcon", "WeChatMainWnd", "WeixinMainWnd"]:
        hwnd = user32.FindWindowW(cls, None)
        if hwnd:
            return hwnd
    # 标题查找
    hwnd = user32.FindWindowW(None, "微信")
    return hwnd if hwnd else None


def bring_to_front(hwnd: int):
    """将窗口置顶"""
    SW_SHOW = 5
    SW_RESTORE = 9
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)


def get_window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def send_key_combo(*keys: str):
    """发送键盘组合键"""
    import pyautogui
    pyautogui.hotkey(*keys)


def type_text(text: str):
    """模拟键盘输入文字 (使用剪贴板方式避免编码问题)"""
    import pyautogui
    import pyperclip
    pyperclip.copy(text)
    time.sleep(0.1)
    pyautogui.hotkey('ctrl', 'v')


def get_clipboard() -> str:
    """读取剪贴板"""
    import pyperclip
    try:
        return pyperclip.paste()
    except Exception:
        return ""


# ── 日志 ─────────────────────────────────────────────────────
def log(level: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    c = {"I": "\033[94m", "✓": "\033[92m", "⚠": "\033[93m", "✗": "\033[91m"}.get(level, "")
    print(f"{c}[{ts} {level}]\033[0m {msg}")


# ═══════════════════════════════════════════════════════════
# 微信操控器 (键盘模拟)
# ═══════════════════════════════════════════════════════════

class WeChatOperator:
    """通过键盘快捷键操控微信"""

    def __init__(self):
        self.hwnd: Optional[int] = None
        self.current_chat: str = ""
        self._msg_hashes = set()   # 已处理（读取过）的消息
        self._sent_hashes = set()  # 自己发送过的消息（防止 AI 回复被当成客户消息读回）
        self._sent_file = Path(__file__).resolve().parent / "wechat_sent_hashes.txt"
        self._load_sent_hashes()

    def _load_sent_hashes(self):
        """加载历史已发送记录 (持久化, 重启后仍能识别自己发过的消息, 防自我循环)"""
        try:
            if self._sent_file.exists():
                for line in self._sent_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.isdigit():
                        self._sent_hashes.add(int(line))
                log("I", f"已加载 {len(self._sent_hashes)} 条历史发送记录")
        except Exception as e:
            log("⚠", f"加载历史发送记录失败: {e}")

    def _save_sent_hashes(self):
        """持久化已发送记录"""
        try:
            self._sent_file.write_text(
                "\n".join(str(h) for h in self._sent_hashes),
                encoding="utf-8",
            )
        except Exception as e:
            log("⚠", f"保存发送记录失败: {e}")

    def init(self) -> bool:
        self.hwnd = find_wechat_window()
        if not self.hwnd:
            log("✗", "未找到微信窗口 — 请打开微信主界面")
            return False
        title = get_window_title(self.hwnd)
        log("✓", f"微信窗口: hwnd={self.hwnd:#x} \"{title}\"")
        return True

    def get_title(self) -> str:
        if self.hwnd:
            return get_window_title(self.hwnd)
        return ""

    def focus(self):
        """确保微信在前台"""
        if self.hwnd:
            bring_to_front(self.hwnd)

    def read_current_chat(self) -> str:
        """
        读取当前聊天窗口的最新客户消息。

        策略: 点击消息区域 → Ctrl+A → Ctrl+C → 读取剪贴板
        从最后一条消息往前找「第一条未被处理过、且不是自己发送的」消息。

        修复: 旧实现只取最后一行——AI 刚发出的回复会排在最下方，
        被当成客户消息读回，形成"AI 回复→触发 AI 再回复"的自我循环。
        现在跳过自己发送过的消息（_sent_hashes），再往前找真正的客户消息。
        """
        import pyautogui

        self.focus()
        time.sleep(0.2)

        # 点击消息区域 (屏幕中央偏右)
        screen_w, screen_h = pyautogui.size()
        pyautogui.click(screen_w // 2 + 100, screen_h // 2 - 50)
        time.sleep(0.2)

        # Ctrl+A 全选 → Ctrl+C 复制
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.15)
        pyautogui.hotkey('ctrl', 'c')
        time.sleep(0.3)

        clipboard = get_clipboard()
        if not clipboard:
            return ""

        # 从最后一条往前找未处理且非己方的消息
        lines = [l.strip() for l in clipboard.split('\n') if l.strip()]
        for text in reversed(lines):
            h = hash(text)
            if h in self._sent_hashes:
                continue  # 自己刚发的回复，跳过
            if h in self._msg_hashes:
                continue  # 已处理过，跳过
            self._msg_hashes.add(h)
            if len(self._msg_hashes) > 500:
                self._msg_hashes.clear()
            return text

        return ""  # 没有新客户消息

    def send_reply(self, text: str) -> bool:
        """
        在当前聊天窗口发送回复。

        策略: 点击输入框 → 清空 → 粘贴文字 → Enter 发送
        """
        import pyautogui

        self.focus()
        time.sleep(0.2)

        # 点击输入框 (屏幕下方)
        screen_w, screen_h = pyautogui.size()
        pyautogui.click(screen_w // 2, screen_h - 80)
        time.sleep(0.2)

        # 清空 + 粘贴 + 发送
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.05)
        type_text(text)
        time.sleep(0.2)
        pyautogui.press('enter')

        # 记录本次发送的消息: 防止下次轮询把 AI 自己的回复当成客户消息 (并持久化)
        self._sent_hashes.add(hash(text.strip()))
        if len(self._sent_hashes) > 200:
            # 保留最近 200 条，避免无限增长
            self._sent_hashes = set(list(self._sent_hashes)[-200:])
        self._save_sent_hashes()

        log("✓", f"📨 已发送: '{text[:40]}...'")
        return True

    def navigate_to_chat(self, contact: str) -> bool:
        """
        用 Ctrl+F 搜索并打开聊天。
        """
        self.focus()
        time.sleep(0.2)

        # Ctrl+F 搜索
        pyautogui.hotkey('ctrl', 'f')
        time.sleep(0.3)

        # 输入联系人名称
        type_text(contact)
        time.sleep(0.5)

        # Enter 打开
        pyautogui.press('enter')
        time.sleep(0.5)

        self.current_chat = contact
        return True

    @staticmethod
    def _is_pseudo_name(name: str) -> bool:
        """
        判断是否为伪联系人名 (UI 模式未识别出真实客户)。

        伪名不触发 Ctrl+F 导航 — 直接在用户当前打开的聊天窗口发送,
        避免把用户切好的窗口搞乱。
        """
        if not name:
            return True
        if name in ("微信联系人", "未知", "unknown"):
            return True
        if name.startswith("wxid_") or name.startswith("wx_"):
            return True
        return False

    def check_unread(self) -> bool:
        """
        检查是否有未读消息。

        微信窗口标题带未读计数时会变成类似 "微信(3)" 的格式。
        """
        title = self.get_title()
        # 检查标题括号中的数字
        import re
        match = re.search(r'微信\((\d+)\)', title)
        if match:
            return int(match.group(1)) > 0
        return False


# ═══════════════════════════════════════════════════════════
# 桥接器
# ═══════════════════════════════════════════════════════════

class UIBridge:
    def __init__(self, account_id: str, gateway_url: str, debug: bool = False, token: str = ""):
        self.account_id = account_id
        self.gateway_url = gateway_url
        self.token = token
        self.wechat = WeChatOperator()
        self.ws: Optional[websockets.ClientConnection] = None
        self._running = False
        self._send_queue = asyncio.Queue()
        self.debug = debug

    async def start(self):
        log("I", "🚀 微信 UI 桥接器启动")

        if not self.wechat.init():
            return

        self._running = True
        log("I", "📡 开始监控...")

        try:
            # 方式1: 轮询特定聊天
            await self._poll_loop()
        except KeyboardInterrupt:
            pass
        finally:
            log("I", "🛑 桥接器已停止")

    async def _connect_gateway(self) -> bool:
        """连接 WSS Gateway"""
        # 鉴权: 配置了 API Token 时拼到 URL query (服务器 process_request 握手校验)
        url = f"{self.gateway_url}/ws/hook/{self.account_id}"
        if self.token:
            url += f"?token={self.token}"
        try:
            self.ws = await websockets.connect(url, ping_interval=25)
            log("✓", f"已连接网关: {url}")
            return True
        except Exception as e:
            log("⚠", f"网关连接失败: {e}")
            return False

    async def _poll_loop(self):
        """
        主轮询循环:
        1. 检查网关连接
        2. 检查微信窗口标题 (是否有未读消息)
        3. 如果有指定聊天, 则读取并推送新消息
        4. 同时处理待发送队列
        """
        # 首次连接
        await self._connect_gateway()

        poll_count = 0
        read_interval = 4  # 每 N 次轮询才读取消息 (避免频繁操作)

        while self._running:
            try:
                # ── 网关保持 ──────────────────────────────
                if not self.ws:
                    await self._connect_gateway()
                    await asyncio.sleep(1)
                    continue

                # 检查网关指令
                try:
                    raw = await asyncio.wait_for(self.ws.recv(), timeout=0.3)
                    data = json.loads(raw)
                    if data.get("type") == "SEND_MSG":
                        sd = data.get("data", {})
                        text = sd.get("text", "")
                        to_user = sd.get("to_user", "")
                        if text:
                            # 修复: 伪名 (UI 模式未识别真实客户) 不导航!
                            # 之前 Ctrl+F 搜索"微信联系人"会把用户切好的聊天窗口搞乱
                            if to_user and to_user != self.wechat.current_chat and not self._is_pseudo_name(to_user):
                                self.wechat.navigate_to_chat(to_user)
                            # 发送到当前窗口
                            self.wechat.send_reply(text)
                    elif data.get("type") == "ping":
                        await self.ws.send(
                            json.dumps({"type": "pong", "ts": time.time()})
                        )
                except asyncio.TimeoutError:
                    pass
                except websockets.exceptions.ConnectionClosed:
                    log("⚠", "网关断开")
                    self.ws = None
                    continue

                # ── 消息读取 ──────────────────────────────
                poll_count += 1
                if poll_count % read_interval == 0:
                    # 修复: 微信 4.x 窗口标题不带未读计数 (标题恒为"微信"),
                    # check_unread() 永远 False 导致不读消息 → 始终轮询读取
                    # 安全性: read_current_chat 有 _msg_hashes 去重, 已读消息不会重复推送
                    has_unread = self.wechat.check_unread()  # 3.x 标题带计数时仍可加速判断
                    if self.debug:
                        log("I", f"轮询 #{poll_count} | 标题=\"{self.wechat.get_title()}\" | 未读={has_unread}")

                    # 微信 4.x: 标题无未读计数, 始终读取当前聊天窗口
                    if has_unread or self.debug or True:
                        # 读取当前聊天
                        text = self.wechat.read_current_chat()
                        if text and len(text) > 1:
                            await self._push_message(text)

                await asyncio.sleep(1.5)

            except Exception as e:
                log("⚠", f"轮询异常: {type(e).__name__}: {e}")
                await asyncio.sleep(3)

    async def _push_message(self, text: str):
        """推送消息到 Gateway"""
        if not self.ws:
            return

        payload = json.dumps({
            "event": "ON_RECV_MSG",
            "data": {
                "from_user": self.wechat.current_chat or "微信联系人",
                "to_user": self.account_id,
                "content": text,
                "is_group": False,
                "msg_id": f"ui_{int(time.time() * 1000)}",
                "timestamp": time.time(),
            },
        }, ensure_ascii=False)

        try:
            await self.ws.send(payload)
            log("✓", f"📤 新消息: \"{text[:40]}...\"")
        except Exception as e:
            log("✗", f"推送失败: {e}")


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="无界AI · 微信 UI 桥接器")
    parser.add_argument("--account", "-a", default="sales_01")
    parser.add_argument("--gateway", "-g", default="ws://127.0.0.1:8765")
    parser.add_argument("--token", default="", help="API Token (服务器启用鉴权后必填)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"""
{'=' * 55}
  🤖 无界AI · 微信 UI 桥接器
{'=' * 55}
  账号: {args.account}
  网关: {args.gateway}/ws/hook/{args.account}
  鉴权: {'✅ 已启用 (token 已配置)' if args.token else '⚠️ 未配置 token (服务器启用鉴权后将无法连接)'}
  模式: Win32 + 键盘模拟 (不注入, 不封号)
{'=' * 55}

📌 使用说明:
  1. 微信已登录, 主窗口可见 (不要最小化!)
  2. 打开你要监控的聊天对话框
  3. 桥接器会定期检测未读消息并推送到AI审核
  4. 审核通过后自动在当前聊天窗口发送回复

⚡ 限制:
  - 桥接器运行时请不要操作鼠标键盘
  - 需要微信窗口在前台可见
  - 首次使用建议 --debug 模式
""")

    # 安装检查
    try:
        import pyautogui
        import pyperclip
    except ImportError:
        print("请安装依赖: pip install pyautogui pyperclip pywin32")
        sys.exit(1)

    bridge = UIBridge(args.account, args.gateway, args.debug, token=args.token)
    asyncio.run(bridge.start())
