import json
import tempfile
import unittest


class ConfigTests(unittest.TestCase):
    def test_company_websites_include_defaults_and_configured_additions(self):
        from popday.config import load_config

        with tempfile.NamedTemporaryFile("w", suffix=".json") as config_file:
            json.dump(
                {
                    "company_websites": {
                        "Example Co": "https://example.com/",
                    }
                },
                config_file,
            )
            config_file.flush()

            config = load_config(config_file.name)

        self.assertEqual(
            config.company_websites["HARMONIC INC."],
            "https://www.harmonicinc.com/",
        )
        self.assertEqual(config.company_websites["Example Co"], "https://example.com/")


if __name__ == "__main__":
    unittest.main()
