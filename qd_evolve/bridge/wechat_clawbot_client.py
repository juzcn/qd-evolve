"""iLink ClawBot protocol client for WeChat message bridge.

Extracted from SiverKing/weixin-ClawBot-API (MIT License).
Handles QR login, long-poll message receive, and message send.
All AI / reconnection / command-routing logic is stripped.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import random
import time
import urllib.request
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "2.4.3"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = str((2 << 16) | (4 << 8) | 3)
BOT_AGENT = "qd-evolve-wechat/1.0.0 (python)"


def _make_headers(token: str | None = None) -> dict:
    uin = str(random.randint(0, 0xFFFFFFFF))
    headers: dict = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": base64.b64encode(uin.encode()).decode(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _base_info() -> dict:
    return {
        "channel_version": CHANNEL_VERSION,
        "bot_agent": BOT_AGENT,
    }


# ── QR code terminal rendering ──

def _render_terminal_qr(content: str) -> None:
    if not content:
        return
    print("\n扫码地址:", content)
    if content.startswith("http") and _render_terminal_image_from_url(content):
        return
    _render_generated_qr(content)


def _render_terminal_image_from_url(url: str) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        image = Image.open(io.BytesIO(data)).convert("L")
        max_width = 72
        scale = max(1, int(image.width / max_width))
        width = max(1, int(image.width / scale))
        height = max(1, int(image.height / scale))
        image = image.resize((width, height))
        print()
        for y in range(height):
            row_chars: list[str] = []
            for x in range(width):
                pixel = image.getpixel((x, y))
                row_chars.append("██" if isinstance(pixel, int) and pixel < 128 else "  ")
            print("".join(row_chars))
        print()
        return True
    except Exception as e:
        logger.debug("QR image render from URL failed: %s", e)
        return False


def _render_generated_qr(content: str) -> None:
    try:
        import qrcode
    except ImportError:
        print("未安装 qrcode/Pillow，无法在终端渲染二维码")
        return

    qr = qrcode.QRCode(border=1)
    qr.add_data(content)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    print()
    for row in matrix:
        print("".join("██" if cell else "  " for cell in row))
    print()


# ── Client ──

class WechatClawbotClient:
    """Async client for the WeChat iLink ClawBot protocol.

    Usage::

        client = WechatClawbotClient()
        if not await client.try_restore_session():
            result = await client.login()
            await client.start(result["bot_token"], result.get("baseurl", ""))
            client.save_session()

        while True:
            for msg in await client.poll_updates():
                text = msg["item_list"][0]["text_item"]["text"]
                await client.send_message(
                    msg["from_user_id"], msg["context_token"], f"echo: {text}"
                )

        await client.stop()
    """

    SESSION_MAX_AGE = 23 * 3600  # re-login if older than 23 hours

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._bot_token: str = ""
        self._base_url: str = BASE_URL
        self._get_updates_buf: str = ""
        self._typing_tickets: dict[str, str] = {}
        self.last_contact: dict[str, str | None] = {"from_id": None, "context_token": None}

    # ── Login ──

    async def login(self, base_url: str = "") -> dict:
        """Full QR login flow. Displays QR in terminal, polls until scanned.

        Returns ``{"bot_token": str, "baseurl": str}``.
        """
        url = base_url or BASE_URL
        refresh_count = 0
        max_refresh = 3

        while True:
            data = await self._fetch_qrcode(url)
            qrcode = data["qrcode"]
            qrcode_img = data.get("qrcode_img_content", "")

            print("qrcode:", qrcode)
            _render_terminal_qr(str(qrcode_img or qrcode))
            print("等待微信扫码...")

            result = await self._wait_login_confirmation(qrcode, url)
            if result.get("bot_token"):
                return result
            if result.get("already_connected"):
                logger.debug("Server reports already connected; refreshing QR code")
            elif result.get("expired"):
                print("二维码已过期，正在重新生成...")
            elif result.get("verify_code_blocked"):
                print("多次输入配对码错误，正在刷新二维码...")
            elif result.get("timeout"):
                print("登录等待超时，正在重新生成二维码...")

            refresh_count += 1
            if refresh_count >= max_refresh:
                raise RuntimeError("二维码多次失效或登录失败，请稍后重试")

    async def _fetch_qrcode(self, base_url: str) -> dict:
        body = {"local_token_list": []}
        data = await self._api_post("ilink/bot/get_bot_qrcode?bot_type=3", body, base_url)
        if data.get("qrcode"):
            return data
        logger.debug("POST did not return qrcode, trying GET fallback")
        return await self._api_get("ilink/bot/get_bot_qrcode?bot_type=3", base_url)

    async def _poll_login_status(
        self, qrcode: str, base_url: str, verify_code: str | None = None
    ) -> dict:
        endpoint = f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode, safe='')}"
        if verify_code:
            endpoint += f"&verify_code={quote(verify_code, safe='')}"
        status = await self._api_get(endpoint, base_url)
        state = status.get("status", "")

        if state == "confirmed" or status.get("bot_token"):
            return {
                "bot_token": status.get("bot_token"),
                "baseurl": status.get("baseurl") or status.get("base_url") or base_url,
                "ilink_bot_id": status.get("ilink_bot_id"),
                "ilink_user_id": status.get("ilink_user_id"),
            }
        if state == "binded_redirect" or status.get("binded_redirect"):
            return {"already_connected": True}
        if state == "expired":
            return {"expired": True}
        if state == "scaned_but_redirect":
            redirect_host = status.get("redirect_host")
            if redirect_host:
                return {"redirect_base": f"https://{redirect_host}"}
            return {}
        if state == "scaned":
            return {"scanned": True, "verify_code_accepted": bool(verify_code)}
        if state in ("need_verifycode", "verify_code_blocked") or status.get("need_verifycode"):
            if state == "verify_code_blocked":
                return {"verify_code_blocked": True}
            return {"need_verifycode": True, "retry_verifycode": bool(verify_code)}
        if state and state != "wait":
            logger.debug("Login status: %s, raw: %s", state, status)

        return {}

    async def _wait_login_confirmation(
        self, qrcode: str, base_url: str, timeout: float = 600
    ) -> dict:
        deadline = asyncio.get_event_loop().time() + timeout
        current_base_url = base_url
        pending_verify_code: str | None = None
        scanned_printed = False

        while True:
            if asyncio.get_event_loop().time() >= deadline:
                return {"timeout": True}

            try:
                result = await self._poll_login_status(
                    qrcode, current_base_url, pending_verify_code
                )
            except Exception as e:
                logger.debug("Poll login status failed: %s", e)
                await asyncio.sleep(1)
                continue

            if result.get("bot_token"):
                return result
            if result.get("already_connected") or result.get("expired"):
                return result
            if result.get("verify_code_blocked"):
                return result
            if result.get("redirect_base"):
                current_base_url = result["redirect_base"]
                logger.debug("Switching poll node to: %s", current_base_url)
                continue
            if result.get("scanned"):
                if pending_verify_code and result.get("verify_code_accepted"):
                    pending_verify_code = None
                if not scanned_printed:
                    print("已扫码，等待手机端确认...")
                    scanned_printed = True
            if result.get("need_verifycode"):
                prompt = (
                    "你输入的数字不匹配，请重新输入: "
                    if result.get("retry_verifycode")
                    else "请输入手机微信显示的数字配对码: "
                )
                pending_verify_code = input(prompt).strip()
                continue

            await asyncio.sleep(1)

    # ── Session lifecycle ──

    async def start(self, bot_token: str, base_url: str = "") -> None:
        """Set credentials and prepare session. Call after ``login()``.

        Replaces any session created during login with a fresh one
        configured for long-polling timeouts.
        """
        self._bot_token = bot_token
        self._base_url = base_url or BASE_URL
        # Close session from login phase, create fresh one for message loop
        if self._session is not None:
            await self._session.close()
        timeout = aiohttp.ClientTimeout(total=120)
        self._session = aiohttp.ClientSession(timeout=timeout)

    async def stop(self) -> None:
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None

    # ── Session persistence ──

    async def try_restore_session(self, saved: dict | None = None) -> bool:
        """Restore session from a saved dict. Returns True if valid."""
        if not saved:
            return False

        saved_at = saved.get("saved_at", 0)
        if time.time() - saved_at > self.SESSION_MAX_AGE:
            logger.debug("Saved session expired (>%sh old)", self.SESSION_MAX_AGE // 3600)
            return False

        bot_token = saved.get("bot_token")
        base_url = saved.get("base_url", BASE_URL)
        if not bot_token:
            return False

        await self.start(bot_token, base_url)
        logger.debug("Session restored from saved dict")
        return True

    def get_session_dict(self) -> dict:
        """Return current session data suitable for persistence."""
        return {
            "bot_token": self._bot_token,
            "base_url": self._base_url,
            "saved_at": time.time(),
        }

    # ── Message operations ──

    async def poll_updates(self) -> list[dict]:
        """Long-poll ``/ilink/bot/getupdates``. Returns list of message dicts.

        Each message dict has: ``from_user_id``, ``context_token``,
        ``item_list[0].text_item.text``, ``message_type``.
        """
        result = await self._api_post(
            "ilink/bot/getupdates",
            {"get_updates_buf": self._get_updates_buf, "base_info": _base_info()},
        )
        self._get_updates_buf = result.get("get_updates_buf") or self._get_updates_buf
        return result.get("msgs") or []

    async def send_message(self, to_user_id: str, context_token: str, text: str) -> dict:
        """Send a text message to the WeChat user."""
        client_id = f"qd-evolve-wechat-{random.randint(0, 0xFFFFFFFF):08x}"
        return await self._api_post(
            "ilink/bot/sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user_id,
                    "client_id": client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context_token,
                    "item_list": [{"type": 1, "text_item": {"text": text}}],
                },
                "base_info": _base_info(),
            },
        )

    async def send_typing(
        self, to_user_id: str, context_token: str, status: int = 1
    ) -> dict | None:
        """Send typing indicator. ``status=1`` to show, ``status=2`` to hide."""
        ticket = await self._ensure_typing_ticket(to_user_id, context_token)
        if not ticket:
            return None
        return await self._api_post(
            "ilink/bot/sendtyping",
            {
                "ilink_user_id": to_user_id,
                "typing_ticket": ticket,
                "status": status,
                "base_info": _base_info(),
            },
        )

    async def _ensure_typing_ticket(self, user_id: str, context_token: str) -> str:
        if user_id in self._typing_tickets:
            return self._typing_tickets[user_id]
        cfg = await self._api_post(
            "ilink/bot/getconfig",
            {
                "ilink_user_id": user_id,
                "context_token": context_token,
                "base_info": _base_info(),
            },
        )
        ticket = cfg.get("typing_ticket", "")
        self._typing_tickets[user_id] = ticket
        return ticket

    # ── Internal helpers ──

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=120)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _api_get(self, path: str, base_url: str = "") -> dict:
        url = f"{base_url or self._base_url}/{path}"
        async with self._get_session().get(url, headers=_make_headers(self._bot_token)) as res:
            text = await res.text()
            logger.debug("[GET %s] HTTP %s → %s", path, res.status, text[:200])
            try:
                return json.loads(text)
            except Exception:
                return {}

    async def _api_post(self, path: str, body: dict, base_url: str = "") -> dict:
        url = f"{base_url or self._base_url}/{path}"
        async with self._get_session().post(
            url, json=body, headers=_make_headers(self._bot_token)
        ) as res:
            text = await res.text()
            logger.debug("[POST %s] HTTP %s → %s", path, res.status, text[:200])
            try:
                return json.loads(text)
            except Exception:
                return {}
