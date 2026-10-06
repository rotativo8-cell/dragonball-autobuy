import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from playwright.async_api import async_playwright
from src.test_jumpichiban_browser import URL, NAME, check_page, diagnose


class JumpDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'test.png'
        self.env = patch.dict(os.environ, {'DRY_RUN':'true','MAX_QUANTITY':'1'})
        self.env.start()
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch()
        self.page = await self.browser.new_page()
        self.status = 200
        self.requests = []
        self.set_html('OutOfStock', True)
        async def respond(route):
            self.requests.append(route.request.method)
            await route.fulfill(status=self.status, content_type='text/html', body=self.html)
        await self.page.route('**/*', respond)

    def set_html(self, stock, disabled):
        data = {'@type':'Product','name':NAME,'offers':{'url':URL,'price':'104.00','priceCurrency':'USD','availability':'https://schema.org/'+stock}}
        self.html = '<title>Ichiban</title><h1>'+NAME+'</h1><script type="application/ld+json">'+json.dumps(data)+'</script><form action="/cart/add"><button name="add" '+('disabled' if disabled else '')+' onclick="window.clicked=true">Add to cart</button></form>'

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        self.env.stop()
        self.temp.cleanup()

    async def test_out_of_stock(self):
        result = await check_page(self.page, self.path)
        self.assertEqual(result['access'], 'OK')
        self.assertEqual(result['stock'], 'OUT_OF_STOCK')
        self.assertEqual(result['price'], '104.00 USD')
        self.assertTrue(result['screenshot_saved'])

    async def test_available_never_clicks(self):
        for stock in ('InStock','PreOrder'):
            self.set_html(stock, False)
            result = await check_page(self.page, self.path)
            self.assertEqual(result['access'], 'OK')
            self.assertTrue(result['button'])
            self.assertFalse(await self.page.evaluate('Boolean(window.clicked)'))
        self.assertEqual(self.requests, ['GET','GET'])

    async def test_contradiction_stops(self):
        self.set_html('OutOfStock', False)
        result = await check_page(self.page, self.path)
        self.assertEqual(result['access'], 'ERROR')
        self.assertIn('contradictorios',result['reason'])

    async def test_challenge_no_retry(self):
        self.status = 403
        self.html = '<title>Just a moment</title><body>Verify you are human</body>'
        result = await check_page(self.page, self.path)
        self.assertTrue(result['challenge'])
        self.assertEqual(result['access'], 'ERROR')
        self.assertEqual(self.requests, ['GET'])

    async def test_safety_before_launch(self):
        for env in ({'DRY_RUN':'false'}, {'MAX_QUANTITY':'2'}):
            with patch.dict(os.environ, env), patch('src.test_jumpichiban_browser.async_playwright') as browser:
                result = await diagnose()
                browser.assert_not_called()
                self.assertEqual(result['access'], 'ERROR')

    async def test_access_restricted_even_with_http_200(self):
        self.html = '<title>Dragon Ball</title><h2>Access Restricted</h2><p>For security reasons, using browser developer tools is not permitted on this site.</p>'
        result = await check_page(self.page, self.path)
        self.assertEqual(result['http'], '200')
        self.assertEqual(result['access'], 'ERROR')
        self.assertIn('Access Restricted', result['reason'])
        self.assertEqual(self.requests, ['GET'])
