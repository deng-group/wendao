from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from wendao.bibtex import build, parse, person
from wendao.cli import main
from wendao.web import STATIC_DIR

BIB = r"""
@comment{ignored}
@article{doi:10.1/a,
  title = {Hydrophobic Composites for {CO<sub>2</sub>} Capture},
  author = {Deng, Zeyu and Cheetham, Anthony K.* and Li, He},
  journal = "J. Am. Chem. Soc.",
  year = 2025,
  doi = {https://doi.org/10.1/a},
  keywords = {carbon capture, MOF, thesis}
}
@article{b2024,
  title = {Na-ion {B}atteries with Sch\"{o}n Cathodes},
  author = {Cheetham, Anthony K. and Deng, Zeyu},
  journal = {Nature},
  year = {2024},
  keywords = {batteries}
}
@phdthesis{c2020,
  title = {A Thesis},
  author = {Deng, Zeyu},
  school = {University of Cambridge},
  year = {2020},
  keywords = {thesis}
}
"""


class BibtexTest(unittest.TestCase):
    def graph(self, **options):
        return build(parse(BIB), **options)

    def test_reads_entries_and_fields(self):
        entries = parse(BIB)
        self.assertEqual([e["key"] for e in entries], ["doi:10.1/a", "b2024", "c2020"])
        self.assertEqual(entries[0]["fields"]["journal"], "J. Am. Chem. Soc.")
        self.assertEqual(entries[0]["fields"]["year"], "2025")
        self.assertEqual(person("Cheetham, Anthony K.*"), "Anthony K. Cheetham")

    def test_builds_themes_papers_and_people(self):
        graph = self.graph(themes={"mof": "MOFs", "thesis": None}, me="Zeyu Deng", min_shared=2)
        nodes = {n["id"]: n for n in graph["nodes"]}
        paper = nodes["paper:doi:10.1/a"]
        self.assertEqual(paper["title"], "Hydrophobic Composites for CO<sub>2</sub> Capture")
        self.assertEqual(paper["label"], "Hydrophobic Composites for CO2 Capture")
        self.assertEqual(paper["url"], "https://doi.org/10.1/a")
        self.assertEqual((paper["year"], paper["venue"]), (2025, "J. Am. Chem. Soc."))
        self.assertEqual(nodes["paper:b2024"]["label"], "Na-ion Batteries with Schön Cathodes")
        self.assertEqual(sorted(n["label"] for n in graph["nodes"] if n["type"] == "theme"), ["Batteries", "Carbon Capture", "MOFs"])
        people = [n for n in graph["nodes"] if n["type"] == "person"]
        self.assertEqual([(p["label"], p["papers"]) for p in people], [("Anthony K. Cheetham", 2)])  # not me; He Li has 1
        self.assertNotIn("paper:c2020", nodes)  # its only keyword is skipped, so it links to nothing
        self.assertEqual(graph["format"], "wendao-graph/1")
        self.assertTrue(graph["types"]["theme"]["hub"])

    def test_command(self):
        with tempfile.TemporaryDirectory() as folder:
            bib = Path(folder) / "papers.bib"
            bib.write_text(BIB, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(["bibtex", str(bib), "-o", str(Path(folder) / "graph.json"), "--me", "Zeyu Deng",
                      "--theme", "mof=MOFs", "--skip", "thesis", "--min-shared", "2"])
            self.assertIn("2 papers, 3 themes, 1 collaborators", output.getvalue())
            graph = json.loads((Path(folder) / "graph.json").read_text(encoding="utf-8"))
            self.assertIn("theme:mofs", {n["id"] for n in graph["nodes"]})


class EmbedFilesTest(unittest.TestCase):
    def test_bundle_is_packaged_and_installed(self):
        script = (STATIC_DIR / "embed" / "wendao-graph.js").read_text(encoding="utf-8")
        self.assertIn("window.WendaoGraph", script)
        self.assertNotIn("fetch(\"/api", script)  # no server calls
        self.assertIn("Powered by", script)
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(["embed", "install", str(Path(folder) / "assets")])
            self.assertTrue((Path(folder) / "assets" / "wendao-graph.js").is_file())
            self.assertTrue((Path(folder) / "assets" / "wendao-graph.css").is_file())
            self.assertIn("data-wendao-graph", output.getvalue())


if __name__ == "__main__":
    unittest.main()
