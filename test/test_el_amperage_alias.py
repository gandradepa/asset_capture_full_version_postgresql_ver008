"""Regression test for EL amperage alias precedence.

The EL review form edits ``Ampere`` while ``Amperage Rating`` is the
Planon-facing alias synchronized from it. If the alias is read first, a stale
``Amperage Rating`` value can overwrite the reviewer-submitted ``Ampere`` value
during save.
"""

from __future__ import annotations

import ast
import pathlib
import unittest


class ElAmperageAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source_path = (
            pathlib.Path(__file__).resolve().parents[1]
            / "review"
            / "Asset_dashboard_browser_EL"
            / "Asset_dashboard_EL.py"
        )
        cls.source_text = cls.source_path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source_text)

    def test_amperage_resolver_prefers_editable_ampere_field(self) -> None:
        target = None
        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_get_el_amperage_value":
                target = node
                break
        self.assertIsNotNone(target, "_get_el_amperage_value was not found")

        key_order = None
        for node in ast.walk(target):
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
                values = [
                    item.value
                    for item in node.iter.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                ]
                if "Ampere" in values and "Amperage Rating" in values:
                    key_order = values
                    break

        self.assertEqual(key_order, ["Ampere", "Amperage Rating"])

    def test_save_uses_submitted_ampere_even_when_blank(self) -> None:
        self.assertIn('form_ampere_raw = request.form.get("Ampere")', self.source_text)
        self.assertIn(
            "normalized_amperage = form_ampere_value if form_ampere_raw is not None else _get_el_amperage_value(structured)",
            self.source_text,
        )


if __name__ == "__main__":
    unittest.main()
