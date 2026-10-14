from __future__ import annotations

import contextlib
import io
import os
import tempfile
import time
import unittest
from pathlib import Path

from wendao import workspace as workspace_module
from wendao.cli import main
from wendao.rag.prompts import ASSIGNMENT_HINTS, RULES_FIRST, PromptBuilder, read_teacher_instructions
from wendao.workspace import WorkspaceError

RESULT = {"status": "answerable", "query": "Solve question 3 of Assignment 2", "evidence": [], "temporal_context": None}


class TeacherInstructionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "wendao.toml").write_text('[course]\nname = "Demo"\n', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def builder(self):
        return PromptBuilder.for_workspace(workspace_module.load(self.root))

    def test_no_file_and_the_starter_file_add_nothing(self):
        plain = self.builder().build(RESULT)["messages"][0]["content"]
        self.assertNotIn("teacher's instructions", plain)
        self.assertNotIn(ASSIGNMENT_HINTS, plain)  # "full" unless the teacher chooses hints
        starter = Path(__file__).resolve().parents[1] / "src" / "wendao" / "starter" / "AGENTS.md"
        self.assertEqual(read_teacher_instructions(starter), "")  # only examples, inside a comment

    def test_teacher_instructions_come_before_the_rules_that_win(self):
        (self.root / "AGENTS.md").write_text("<!-- a note to myself -->\nEnd with one check question.\n", encoding="utf-8")
        builder = self.builder()
        system = builder.build(RESULT)["messages"][0]["content"]
        self.assertIn("End with one check question.", system)
        self.assertNotIn("a note to myself", system)
        self.assertLess(system.index("End with one check question."), system.index(RULES_FIRST))
        self.assertTrue(system.startswith("You are the course helper for Demo."))  # Wendao's own rules first

        # an edit is used on the next question, without a restart
        time.sleep(0.01)
        (self.root / "AGENTS.md").write_text("Answer in Chinese.\n", encoding="utf-8")
        os.utime(self.root / "AGENTS.md", (time.time() + 5, time.time() + 5))
        system = builder.build(RESULT)["messages"][0]["content"]
        self.assertIn("Answer in Chinese.", system)
        self.assertNotIn("End with one check question.", system)

    def test_hints_for_graded_work_and_another_file_name(self):
        (self.root / "wendao.toml").write_text('[course]\nname = "Demo"\n[student]\nassignment_help = "hints"\n'
                                               '[model]\ninstructions = "tutor.md"\n', encoding="utf-8")
        (self.root / "tutor.md").write_text("Be brief.", encoding="utf-8")
        system = self.builder().build(RESULT)["messages"][0]["content"]
        self.assertIn(ASSIGNMENT_HINTS, system)
        self.assertIn("Be brief.", system)

        (self.root / "wendao.toml").write_text('[course]\nname = "Demo"\n[student]\nassignment_help = "never"\n', encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "assignment_help"):
            workspace_module.load(self.root)

    def test_init_creates_the_file_and_doctor_reports_it(self):
        course = self.root / "course"
        with contextlib.redirect_stdout(io.StringIO()):
            main(["init", str(course)])
        self.assertTrue((course / "AGENTS.md").is_file())
        self.assertIn('assignment_help = "hints"', (course / "wendao.toml").read_text(encoding="utf-8"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.suppress(SystemExit):
            main(["doctor", "-w", str(course), "--no-model"])
        self.assertIn("hints only for graded work; no instructions of your own", output.getvalue())


if __name__ == "__main__":
    unittest.main()
