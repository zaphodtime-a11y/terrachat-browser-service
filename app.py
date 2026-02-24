"""
TerraChat AI — Browser Service
================================
Servicio centralizado de navegador para todos los agentes.
Corre en un sandbox dedicado con 2GB+ RAM y Playwright siempre activo.

Endpoints:
  POST /browse         — Navega una URL y devuelve texto + screenshot en base64
  POST /screenshot     — Navega una URL y devuelve solo el screenshot
  POST /extract        — Extrae texto limpio de una URL (sin screenshot)
  GET  /healthz        — Health check
  GET  /status         — Estado del servicio (browser activo, requests procesados)

Uso desde los agentes:
  import requests
  resp = requests.post(BROWSER_SERVICE_URL + "/browse", json={"url": "https://..."}, headers={"X-API-Key": KEY})
  data = resp.json()
  screenshot_b64 = data["screenshot"]   # base64 PNG
  text_content   = data["text"]         # texto limpio de la página
"""

import asyncio
import base64
import logging
import os
import time
import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("BrowserService")

# ── Config ─────────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("BROWSER_SERVICE_API_KEY", "terrachat-browser-2026")
PORT    = int(os.environ.get("PORT", 8080))

# ── State ──────────────────────────────────────────────────────────────────────
_playwright = None
_browser    = None
_stats = {
    "requests_total": 0,
    "requests_ok": 0,
    "requests_error": 0,
    "started_at": time.time(),
}


async def _ensure_browser():
    """Launch browser if not already running."""
    global _playwright, _browser
    if _browser and _browser.is_connected():
        return _browser
    log.info("🚀 Launching Chromium...")
    from playwright.async_api import async_playwright
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-sync",
            "--metrics-recording-only",
            "--mute-audio",
            "--no-first-run",
            "--safebrowsing-disable-auto-update",
            "--window-size=1280,800",
        ]
    )
    log.info("✅ Chromium launched")
    return _browser


async def _close_browser():
    global _playwright, _browser
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start browser in background on startup so /healthz responds immediately."""
    # Launch browser in background — don't block startup
    asyncio.create_task(_ensure_browser())
    yield
    await _close_browser()


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="TerraChat Browser Service",
    description="Centralized browser service for TerraChat AI agents",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ─────────────────────────────────────────────────────────────────────
class BrowseRequest(BaseModel):
    url: str
    action: str = "read"          # "read" | "screenshot" | "click" | "fill"
    selector: Optional[str] = None
    input_text: Optional[str] = None
    wait_ms: int = 1500            # Extra wait after page load (ms)
    timeout_ms: int = 20000        # Navigation timeout (ms)
    full_page: bool = False        # Full page screenshot or viewport only
    save_to_agent: Optional[str] = None  # If set, agent_id to save screenshot for


# ── Auth helper ────────────────────────────────────────────────────────────────
def _check_auth(x_api_key: Optional[str]):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Core browse logic ──────────────────────────────────────────────────────────
async def _browse(req: BrowseRequest) -> dict:
    """Core browsing logic. Returns dict with text, screenshot, title, url."""
    global _browser
    _stats["requests_total"] += 1

    # Ensure browser is alive (auto-restart if crashed)
    try:
        browser = await _ensure_browser()
    except Exception as e:
        _stats["requests_error"] += 1
        raise HTTPException(status_code=503, detail=f"Browser unavailable: {e}")

    page = None
    try:
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        # Navigate
        try:
            await page.goto(req.url, wait_until="domcontentloaded", timeout=req.timeout_ms)
        except Exception as nav_err:
            log.warning(f"Navigation warning for {req.url}: {nav_err}")
            # Continue anyway — partial loads are often fine

        # Extra wait for dynamic JS
        if req.wait_ms > 0:
            await page.wait_for_timeout(req.wait_ms)

        title = await page.title()
        result = {
            "url": req.url,
            "title": title,
            "action": req.action,
        }

        # Screenshot (always taken for browse/screenshot actions)
        if req.action in ("screenshot", "read", "click", "fill"):
            screenshot_bytes = await page.screenshot(full_page=req.full_page)
            result["screenshot"] = base64.b64encode(screenshot_bytes).decode("ascii")

        # Text extraction
        if req.action in ("read",):
            try:
                text = await page.inner_text("body")
            except Exception:
                content = await page.content()
                text = re.sub(r'<[^>]+>', ' ', content)
                text = re.sub(r'\s+', ' ', text)
            text = re.sub(r'\n{3,}', '\n\n', text).strip()
            result["text"] = text[:8000] if len(text) > 8000 else text

        # Click action
        elif req.action == "click" and req.selector:
            await page.click(req.selector, timeout=5000)
            await page.wait_for_timeout(1000)
            text = await page.inner_text("body")
            result["text"] = text[:3000]

        # Fill action
        elif req.action == "fill" and req.selector and req.input_text:
            await page.fill(req.selector, req.input_text)
            await page.wait_for_timeout(500)
            result["text"] = f"Filled '{req.selector}' with: {req.input_text[:50]}"

        await context.close()
        _stats["requests_ok"] += 1
        return result

    except HTTPException:
        raise
    except Exception as e:
        _stats["requests_error"] += 1
        if page:
            try:
                await page.close()
            except Exception:
                pass
        log.error(f"Browse error for {req.url}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.get("/healthz")
async def healthz():
    """Always returns 200 immediately — browser may still be warming up."""
    return {"ok": True, "browser_ready": _browser is not None and _browser.is_connected()}


@app.get("/status")
async def status(x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    browser_ok = _browser is not None and _browser.is_connected()
    uptime = int(time.time() - _stats["started_at"])
    return {
        "browser_active": browser_ok,
        "uptime_seconds": uptime,
        **_stats,
    }


@app.post("/browse")
async def browse(req: BrowseRequest, x_api_key: Optional[str] = Header(None)):
    """
    Full browse: navigate URL, return text content + screenshot.
    
    Request body:
      {
        "url": "https://example.com",
        "action": "read",        // "read" | "screenshot" | "click" | "fill"
        "wait_ms": 1500,         // extra wait after load
        "timeout_ms": 20000,     // navigation timeout
        "full_page": false       // full page screenshot?
      }
    
    Response:
      {
        "url": "...",
        "title": "...",
        "text": "...",           // page text (action=read)
        "screenshot": "base64",  // PNG screenshot
        "action": "read"
      }
    """
    _check_auth(x_api_key)
    log.info(f"📖 Browse: {req.url} (action={req.action})")
    return await _browse(req)


@app.post("/screenshot")
async def screenshot_only(req: BrowseRequest, x_api_key: Optional[str] = Header(None)):
    """Navigate URL and return only the screenshot (faster, no text extraction)."""
    _check_auth(x_api_key)
    req.action = "screenshot"
    log.info(f"📸 Screenshot: {req.url}")
    result = await _browse(req)
    return {
        "url": result["url"],
        "title": result["title"],
        "screenshot": result.get("screenshot", ""),
    }


@app.post("/extract")
async def extract_text(req: BrowseRequest, x_api_key: Optional[str] = Header(None)):
    """Navigate URL and return only the text content (no screenshot, faster)."""
    _check_auth(x_api_key)
    req.action = "read"
    log.info(f"📄 Extract: {req.url}")
    result = await _browse(req)
    return {
        "url": result["url"],
        "title": result["title"],
        "text": result.get("text", ""),
    }


@app.post("/restart-browser")
async def restart_browser(x_api_key: Optional[str] = Header(None)):
    """Force restart the browser (useful if it crashes)."""
    _check_auth(x_api_key)
    log.info("🔄 Restarting browser...")
    await _close_browser()
    await _ensure_browser()
    return {"ok": True, "message": "Browser restarted"}


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
