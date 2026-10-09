"""GitHub Marketplace rejects an action whose metadata breaks its listing rules.

Found when publishing v2.1.0: the Marketplace refused the listing because action.yml's
description was over its 125-character limit, and the fix needed a new release, since the
Marketplace reads action.yml from the release's tag.
"""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_repo_invariants as invariants  # noqa: E402


class MarketplaceMetadataTests(unittest.TestCase):

    def test_description_fits_the_marketplace_limit(self):
        description = invariants.action_description(
            (ROOT / "action.yml").read_text(encoding="utf-8"))
        self.assertTrue(description)
        self.assertLess(len(description), 125, description)

    def test_folded_descriptions_are_joined_the_way_yaml_folds_them(self):
        text = "name: X\ndescription: >-\n  first line\n  second line\nauthor: y\n"
        self.assertEqual(invariants.action_description(text), "first line second line")

    def test_plain_descriptions_are_read_too(self):
        self.assertEqual(invariants.action_description("description: short one\n"),
                         "short one")


if __name__ == "__main__":
    unittest.main()
