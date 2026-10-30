"""Offline smoke test run by CI inside the image, without production mounts."""
import asyncio
import fcntl
import os
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    assert os.getuid() == 1000 and os.geteuid() != 0
    for name in ('data', 'logs', 'screenshots'):
        path = Path('/app') / name / 'smoke-test'
        path.write_text('test')
        assert path.read_text() == 'test'
        path.unlink()
    with Path('/app/data/smoke.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content('<h1>Offline smoke test</h1>')
        assert await page.locator('h1').inner_text() == 'Offline smoke test'
        await browser.close()
    print('PASS: UID1000, writable runtime directories, locking and offline Chromium.')


asyncio.run(main())
