import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from playwright.async_api import async_playwright
from src.test_ninnin_browser import check_page, diagnose

HTML = (Path(__file__).parent/'fixtures/ninnin_out_of_stock.html').read_text()


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'ninnin-browser-test.png'
        self.env = patch.dict(os.environ, {'DRY_RUN':'true','MAX_QUANTITY':'1','NINNIN_CURRENCY':'EUR','SCREENSHOT_DIR':self.temp.name})
        self.env.start()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        self.html, self.status = '<title>Nin-Nin test</title>'+HTML, 200
        self.requests = []
        async def respond(route):
            self.requests.append((route.request.method,route.request.url))
            await route.fulfill(status=self.status,content_type='text/html; charset=utf-8',body=self.html)
        await self.page.route('**/*', respond)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()
        self.env.stop()
        self.temp.cleanup()

    async def test_out_of_stock_details_and_screenshot(self):
        result = await check_page(self.page,self.path)
        self.assertEqual(result['access'],'OK')
        self.assertEqual(result['http'],'200')
        self.assertEqual(result['title'],'Nin-Nin test')
        self.assertEqual(result['price'],'94.39 EUR')
        self.assertEqual(result['stock'],'OUT_OF_STOCK')
        self.assertFalse(result['button'])
        self.assertTrue(result['screenshot_saved'])
        self.assertTrue(self.path.exists())

    async def test_in_stock_never_clicks_add(self):
        self.html = self.html.replace('https://schema.org/OutOfStock','https://schema.org/InStock').replace('<span class="exclusive soon"> Soon available </span>', '<button onclick="window.clicked=true">Add to cart</button>')
        result = await check_page(self.page,self.path)
        self.assertEqual(result['stock'],'IN_STOCK')
        self.assertTrue(result['button'])
        self.assertFalse(await self.page.evaluate('Boolean(window.clicked)'))
        self.assertEqual(len(self.requests),1)
        self.assertEqual(self.requests[0][0],'GET')

    async def test_403_without_challenge(self):
        self.status=403
        self.html='<title>403 Forbidden</title>Request forbidden by administrative rules.'
        result=await check_page(self.page,self.path)
        self.assertEqual(result['access'],'ERROR')
        self.assertEqual(result['http'],'403')
        self.assertFalse(result['challenge'])
        self.assertIn('acceso denegado',result['reason'])
        self.assertTrue(result['screenshot_saved'])
        self.assertEqual(len(self.requests),1)

    async def test_cloudflare_no_retry(self):
        self.status=403
        self.html='<title>Just a moment</title>Verify you are human'
        result=await check_page(self.page,self.path)
        self.assertTrue(result['challenge'])
        self.assertEqual(result['access'],'ERROR')
        self.assertEqual(len(self.requests),1)

    async def test_false_dry_run_refuses_browser(self):
        with patch.dict(os.environ,{'DRY_RUN':'false'}),patch('src.test_ninnin_browser.async_playwright') as browser:
            result=await diagnose()
            browser.assert_not_called()
        self.assertEqual(result['access'],'ERROR')
        self.assertIn('DRY_RUN=true',result['reason'])

    async def test_navigation_error_still_screenshots(self):
        await self.page.unroute('**/*')
        await self.page.route('**/*',lambda route: route.abort('failed'))
        result=await check_page(self.page,self.path)
        self.assertEqual(result['access'],'ERROR')
        self.assertTrue(result['screenshot_saved'])
