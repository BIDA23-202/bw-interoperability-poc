import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

import multiroute_gateway as gw


class FrontendPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.object(gw, "DEMO_PASSWORD", None):
            cls.response = TestClient(gw.app).get("/")
        cls.html = cls.response.text

    def test_page_is_served_as_html(self):
        self.assertEqual(self.response.status_code, 200)
        self.assertIn("text/html", self.response.headers["content-type"])

    def test_document_is_complete(self):
        lowered = self.html.lower()
        for marker in ("<!doctype html>", "<body", "<script", "</script>", "</html>"):
            self.assertIn(marker, lowered)

    def test_page_uses_the_quote_flow(self):
        for marker in ("/quotes", "quote_id", "/advance", "/resolve"):
            self.assertIn(marker, self.html)

    def test_every_referenced_element_id_exists(self):
        referenced = set(re.findall(r'\$\("#([\w-]+)"\)', self.html))
        defined = set(re.findall(r'\sid="([\w-]+)"', self.html))
        self.assertTrue(referenced)
        self.assertEqual(referenced - defined, set())


if __name__ == "__main__":
    unittest.main()