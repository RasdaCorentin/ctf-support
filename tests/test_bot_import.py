import importlib
import os
import unittest
from unittest import mock


class BotImportTest(unittest.TestCase):
    def test_import_has_no_side_effect(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            module = importlib.import_module("support.bot")
            self.assertNotIn("DISCORD_TOKEN", os.environ)
        self.assertTrue(module.DRY_RUN)


if __name__ == "__main__":
    unittest.main()
