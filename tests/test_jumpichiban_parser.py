import json
import unittest
from src.test_jumpichiban_browser import NAME, URL, parse_product
from src.shops.base import CriticalError

class JumpParserTests(unittest.TestCase):
    def html(self, **changes):
        offer = dict(url=URL,price='104.00',priceCurrency='USD',availability='https://schema.org/OutOfStock')
        offer.update(changes)
        return '<h1>'+NAME+'</h1><script type="application/ld+json">'+json.dumps(dict(name=NAME, **{'@type':'Product'},offers=offer))+'</script>'

    def test_available_and_unavailable(self):
        for status in ('InStock','OutOfStock','PreOrder'):
            self.assertEqual(parse_product(self.html(availability='https://schema.org/'+status)),(NAME,'104.00 USD',status))

    def test_invalid_offer(self):
        for changes in (dict(url='https://example.com'),dict(price='NaN'),dict(price='-1'),dict(priceCurrency=''),dict(availability='unknown')):
            with self.assertRaises(CriticalError):
                parse_product(self.html(**changes))

    def test_wrong_identity(self):
        with self.assertRaises(CriticalError):
            parse_product(self.html().replace(NAME,'Other product'))

    def test_header_logo_and_animated_product_title(self):
        html = self.html().replace('<h1>'+NAME+'</h1>', '<h1>Jump Ichiban</h1><h1><span>Dragon Ball</span> <span>Visual Adventure Premium Set Vol.2</span></h1>')
        self.assertEqual(parse_product(html)[1], '104.00 USD')
