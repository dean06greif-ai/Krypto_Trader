import asyncio, json, urllib.request
from playwright.async_api import async_playwright

async def main():
    req = urllib.request.Request("http://localhost:8001/api/auth/login",
        data=json.dumps({"username": "Admin", "password": "Dea...eif!/Admin"}).encode(),
        headers={"Content-Type": "application/json"})
    token = json.loads(urllib.request.urlopen(req).read())["token"]
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(viewport={"width": 1900, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)[:200]))
        await page.goto("http://localhost:3000", wait_until="domcontentloaded")
        await page.evaluate(f"localStorage.setItem('admin_token','{token}'); localStorage.setItem('admin_token_ts', String(Date.now()));")
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)
        # Desktop page scroll test
        body_scroll = await page.evaluate("document.documentElement.scrollHeight > window.innerHeight")
        print("page scrollable:", body_scroll)
        await page.locator('.strategy-tab-action').first.click(force=True)
        await page.wait_for_timeout(3500)
        print("ai panel:", await page.locator('[data-testid="ai-trading-panel"]').count())
        # KI-Team
        await page.click('[data-testid="ai-team-toggle"]', force=True)
        await page.wait_for_timeout(3500)
        print("error boundary:", await page.locator('[data-testid="error-boundary"]').count())
        print("team panel:", await page.locator('[data-testid="ai-team-panel"]').count())
        print("role cards:", await page.locator('.ai-role-card').count())
        print("supervisor:", await page.locator('[data-testid="ai-supervisor-panel"]').count())
        await page.screenshot(path="/app/test_reports/team_fixed.jpeg", quality=40, type="jpeg")
        # back to chat -> scroll check
        await page.click('[data-testid="ai-team-toggle"]', force=True)
        await page.wait_for_timeout(1500)
        pos = await page.evaluate("""() => { const el = document.querySelector('[data-testid="ai-chat-area"]');
            return el ? {top: el.scrollTop, h: el.scrollHeight, c: el.clientHeight} : null; }""")
        print("chat scroll:", pos, "atBottom:", pos and (pos['h'] - pos['top'] - pos['c'] < 100))
        # Lessons numbering
        await page.click('[data-testid="ai-learn-toggle"]', force=True)
        await page.wait_for_timeout(3000)
        nos = await page.locator('.ai-lesson-no').count()
        print("lesson number badges:", nos)
        await page.screenshot(path="/app/test_reports/lessons.jpeg", quality=40, type="jpeg")
        print("ERRORS:", errs[:5])
        await b.close()

asyncio.run(main())
