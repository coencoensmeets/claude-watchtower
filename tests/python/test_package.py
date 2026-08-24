"""Every module in the package imports, and imports only what exists.

This catches the one failure the unit tests cannot: a module that names a symbol
in an import statement which the other module does not actually export. Nothing
reports that until the import runs, and the panel's own tests never import
watchtower.http at all — so a wrong import there took the panel down while every
other check stayed green.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import watchtower  # noqa: E402


def package_modules() -> list[str]:
    return sorted(m.name for m in pkgutil.walk_packages(watchtower.__path__, "watchtower."))


class Imports(unittest.TestCase):

    def test_every_module_imports(self):
        for name in package_modules():
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_the_entry_point_imports(self):
        importlib.import_module("server")

    def test_every_name_imported_from_the_package_really_exists(self):
        """`from watchtower.x import y` — is there a y?

        Read statically rather than by importing, so a typo is reported against
        the file that has it rather than wherever the chain happened to break.
        """
        exported = {}
        for path in sorted(ROOT.joinpath("watchtower").rglob("*.py")):
            module = "watchtower." + str(path.relative_to(ROOT / "watchtower")).replace(
                "/", ".").removesuffix(".py").removesuffix(".__init__")
            names = set()
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Assign):
                    names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names |= {(a.asname or a.name).split(".")[0] for a in node.names}
            exported[module.removesuffix(".")] = names

        for path in sorted(ROOT.glob("*.py")) + sorted(ROOT.joinpath("watchtower").rglob("*.py")):
            for node in ast.parse(path.read_text()).body:
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                if node.module not in exported:
                    continue
                for alias in node.names:
                    with self.subTest(where=path.name, imports=alias.name, frm=node.module):
                        self.assertIn(alias.name, exported[node.module],
                                      f"{path.name} imports {alias.name} from {node.module}, "
                                      f"which does not define it")



class Routes(unittest.TestCase):
    """The route table, and whether the README still describes it."""

    def test_every_route_resolves_to_a_real_handler(self):
        from watchtower.http import ROUTES, Handler
        for (method, path), name in ROUTES.items():
            with self.subTest(route=f"{method} {path}"):
                self.assertTrue(callable(getattr(Handler, name, None)),
                                f"{method} {path} names {name}, which Handler does not define")

    def test_the_readme_documents_exactly_the_routes_that_exist(self):
        """A route nobody wrote down is the one nobody maintains."""
        import re
        from watchtower.http import ROUTES

        readme = (ROOT / "README.md").read_text()
        section = readme.split("## API", 1)[1]
        # The path, and then whatever the row shows of the query or body up to
        # the closing backtick. Matching to the backtick alone silently dropped
        # every route documented with a query string on it, which is how four
        # of them went undocumented while this test stayed green.
        documented = set(re.findall(r"`(GET|POST) (/api/[\w/-]*)[^`]*`", section))
        self.assertEqual(documented, set(ROUTES),
                         "the README's API table and the route table disagree")


if __name__ == "__main__":
    unittest.main()
