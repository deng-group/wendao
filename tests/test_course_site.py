from __future__ import annotations

import contextlib
import io
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from test_readers import make_docx, make_pdf, make_pptx

from wendao import course_site
from wendao import workspace as workspace_module
from wendao.cli import main

CONFIG = """[course]
name = "Demo Course"
code = "MSE1234"
term = "2026 Semester 1"
instructor = "Dr. Ada Lovelace"

[source]
path = "materials"             # folder with your notes
file_types = ["pdf", "pptx", "docx", "md", "ipynb"]
exclude = []

[student]
roster = "students.csv"

[[chapters]]
folder = "week2_structures"
name = "Crystal Structures"
description = "Unit cells and symmetry."
"""


class CourseSiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "wendao.toml").write_text(CONFIG, encoding="utf-8")
        (self.root / "students.csv").write_text("email\nstudent@example.edu\n", encoding="utf-8")
        notes = self.root / "materials"
        for folder in ["week1_intro", "week2_structures", ".git"]:
            (notes / folder).mkdir(parents=True)
        (notes / ".env").write_text("OPENAI_API_KEY=sk-secret\n", encoding="utf-8")
        (notes / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        (notes / "week1_intro" / "welcome.md").write_text(
            "# Welcome\n\n![A figure](figure.png)\n\nThe reading list is in the [handout](handout.pdf).\n", encoding="utf-8")
        (notes / "week1_intro" / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        make_pdf(notes / "week1_intro" / "handout.pdf", ["Reading list"])
        make_pptx(notes / "week1_intro" / "lecture10.pptx")
        make_pptx(notes / "week1_intro" / "lecture2.pptx")
        (notes / "week1_intro" / "lecture2.md").write_text("# Lecture 2 notes\n", encoding="utf-8")
        make_docx(notes / "week2_structures" / "manual.docx")
        make_pdf(notes / "week2_structures" / "slides.pdf", ["Space groups: there are 230 (> 200) of them"])
        (notes / "week2_structures" / "demo.ipynb").write_text(json.dumps({
            "cells": [{"cell_type": "markdown", "id": "intro", "metadata": {}, "source": ["# Symmetry demo"]}],
            "metadata": {}, "nbformat": 4, "nbformat_minor": 5}), encoding="utf-8")
        self.workspace = workspace_module.load(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self):
        return course_site.new(self.workspace, self.root / "site")

    def test_makes_a_myst_project_with_chapters_in_teaching_order(self):
        result = self.make()
        site = self.root / "site"
        self.assertEqual(result["chapters"], 2)
        myst = (site / "myst.yml").read_text(encoding="utf-8")
        self.assertIn('title: "Demo Course"', myst)
        self.assertIn('name: "Dr. Ada Lovelace"', myst)
        # [[chapters]] first, then the other folders; pages in natural order (lecture2 before lecture10)
        self.assertLess(myst.index("week2_structures/index.md"), myst.index("week1_intro/index.md"))
        self.assertLess(myst.index("week1_intro/lecture2.md"), myst.index("week1_intro/lecture10.md"))
        # a converted deck never overwrites a page of the same name
        self.assertIn("week1_intro/lecture2-pptx.md", myst)
        self.assertEqual((site / "week1_intro" / "lecture2.md").read_text(encoding="utf-8"), "# Lecture 2 notes\n")
        home = (site / "index.md").read_text(encoding="utf-8")
        self.assertIn('subtitle: "MSE1234 · 2026 Semester 1"', home)
        self.assertIn(":link: week2_structures/index.md", home)
        self.assertIn("- Year: 2026 Semester 1", (site / "syllabus.md").read_text(encoding="utf-8"))
        self.assertIn("| 1 | Crystal Structures |", (site / "calendar.md").read_text(encoding="utf-8"))
        self.assertIn("[Symmetry demo](demo.ipynb)", (site / "week2_structures" / "index.md").read_text(encoding="utf-8"))

    def test_pages_from_slides_word_and_pdf_keep_the_original_as_a_download(self):
        self.make()
        site = self.root / "site"
        deck = (site / "week1_intro" / "lecture10.md").read_text(encoding="utf-8")
        self.assertIn('title: "Phase Diagrams"', deck)
        self.assertIn("{download}`Download the slides (PowerPoint) <lecture10.pptx>`", deck)
        self.assertIn(":::{note} Speaker notes", deck)
        self.assertIn("| Anatase | -0.2 |", deck)
        self.assertNotIn("## Phase Diagrams", deck)  # the first slide's title is the page title
        self.assertTrue((site / "week1_intro" / "lecture10.pptx").is_file())
        manual = (site / "week2_structures" / "manual.md").read_text(encoding="utf-8")
        self.assertIn("## Safety", manual)
        self.assertIn("{download}`Download the document (Word) <manual.docx>`", manual)
        slides = (site / "week2_structures" / "slides.md").read_text(encoding="utf-8")
        self.assertIn(":::{dropdown} Text of the PDF (1 page)", slides)
        self.assertIn("230 (\\> 200)", slides)  # text is escaped so it can't turn into markup

    def test_linked_files_stay_files_and_private_files_are_never_copied(self):
        result = self.make()
        site = self.root / "site"
        self.assertTrue((site / "week1_intro" / "handout.pdf").is_file())
        self.assertFalse((site / "week1_intro" / "handout.md").exists())  # linked from a page: a file, not a page
        self.assertTrue((site / "week1_intro" / "figure.png").is_file())
        copied = {path.relative_to(site).as_posix() for path in site.rglob("*") if path.is_file()}
        self.assertFalse({".env", ".git/config"} & copied)
        self.assertEqual(result["warnings"], [])

    def test_workspace_files_stay_private_when_the_notes_are_the_workspace_itself(self):
        config = (self.root / "wendao.toml").read_text(encoding="utf-8").replace('path = "materials"', 'path = "."')
        (self.root / "wendao.toml").write_text(config, encoding="utf-8")
        (self.root / "questions.json").write_text("[]", encoding="utf-8")
        course_site.new(workspace_module.load(self.root), self.root / "site")
        copied = {path.name for path in (self.root / "site").rglob("*") if path.is_file()}
        self.assertFalse({"wendao.toml", "students.csv", "questions.json", ".env"} & copied)

    def test_wendao_then_reads_the_website(self):
        self.make()
        old = course_site.point_source_at(self.workspace, self.root / "site")
        self.assertEqual(old, "materials")
        settings = tomllib.loads((self.root / "wendao.toml").read_text(encoding="utf-8"))
        self.assertEqual(settings["source"]["path"], "site")
        self.assertTrue(settings["source"]["use_toc"])
        self.assertNotIn("file_types", settings["source"])  # the website's pages are .md, whatever the originals were
        reloaded = workspace_module.load(self.root)
        self.assertEqual(reloaded.source, (self.root / "site").resolve())

        from wendao.ingest import extract

        summary = extract(reloaded)
        self.assertEqual(summary["warnings"], [])
        files = {json.loads(line)["file_path"] for line in reloaded.chunks_path.read_text(encoding="utf-8").splitlines()}
        self.assertIn("week1_intro/lecture10.md", files)
        self.assertFalse(any(name.endswith((".pptx", ".pdf", ".docx")) for name in files))  # originals are not read twice

    def test_refuses_to_overwrite_or_to_wrap_a_myst_site(self):
        self.make()
        with self.assertRaisesRegex(course_site.SiteError, "already has files"):
            self.make()
        (self.root / "materials" / "myst.yml").write_text("version: 1\n", encoding="utf-8")
        with self.assertRaisesRegex(course_site.SiteError, "wendao widget install"):
            course_site.new(self.workspace, self.root / "other")

    def test_build_runs_myst_and_adds_the_widget(self):
        self.make()
        site = self.root / "site"

        def fake_myst(command, cwd, check):
            html = Path(cwd) / "_build" / "html"
            html.mkdir(parents=True)
            (html / "index.html").write_text("<html><head></head><body>Home</body></html>", encoding="utf-8")
            return mock.Mock(returncode=0)

        with mock.patch.object(course_site, "myst_command", return_value=["myst", "build", "--html"]), \
                mock.patch.object(course_site.subprocess, "run", side_effect=fake_myst):
            result = course_site.build(site)
        self.assertEqual(result["pages"], 1)
        self.assertIn("wendao-widget.js", (site / "_build" / "html" / "index.html").read_text(encoding="utf-8"))

        with mock.patch.object(course_site, "myst_command", return_value=None), \
                self.assertRaisesRegex(course_site.SiteError, "Node.js"):
            course_site.build(site)

    def test_command_says_what_to_do_next(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(["site", "new", "-w", str(self.root)])
        text = output.getvalue()
        self.assertIn("Made the course website in site", text)
        self.assertIn('was "materials"', text)
        self.assertIn("wendao site build", text)


if __name__ == "__main__":
    unittest.main()
