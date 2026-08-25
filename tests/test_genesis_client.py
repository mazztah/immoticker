import importlib
import os
import unittest

import genesis_client


class GenesisClientTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("GENESIS_API_KEY", None)
        os.environ.pop("GENESIS_USERNAME", None)
        os.environ.pop("GENESIS_PASSWORD", None)
        importlib.reload(genesis_client)

    def test_token_auth_omits_empty_password(self):
        os.environ["GENESIS_API_KEY"] = "token-123"
        importlib.reload(genesis_client)

        self.assertTrue(genesis_client.has_genesis_key())
        self.assertEqual(genesis_client._auth_params(), {"username": "token-123"})

    def test_safe_url_masks_genesis_credentials(self):
        os.environ["GENESIS_API_KEY"] = "token-123"
        importlib.reload(genesis_client)

        safe = genesis_client._safe_url(
            "https://genesis.destatis.de/genesisWS/rest/2020/data/table?username=token-123"
        )

        self.assertNotIn("token-123", safe)
        self.assertIn("***", safe)

    def test_parse_ffcsv_strips_markers_and_normalizes_rows(self):
        raw = """Meta;ignored
__DATA__
Zeit;Wert;
2023;10
2024;11;A
__END__
Footer;ignored
"""

        parsed = genesis_client.parse_ffcsv(raw)

        self.assertEqual(parsed["header"], ["Zeit", "Wert", "Spalte 3"])
        self.assertEqual(parsed["rows"], [["2023", "10", ""], ["2024", "11", "A"]])


if __name__ == "__main__":
    unittest.main()
