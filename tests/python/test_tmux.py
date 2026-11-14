"""Reading Claude Code's prompts off a tmux pane.

The screens here were captured from Claude Code 2.1.325 running in tmux. The
readers are what decides whether the panel offers to press a key at all, so
each is checked against the real layout and against near-misses.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import tmux  # noqa: E402

RULE = "─" * 120
DASH = "╌" * 120

PERMISSION = f"""❯ Run exactly this bash command and nothing else: touch probe.txt
● Bash(touch probe.txt)
  ⎿  Waiting…
{RULE}
 Bash command
 Tip: auto mode handles these prompts for you — choose "switch to auto mode" below
 Create empty probe file
{DASH}
 touch probe.txt
{DASH}
 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and always allow access to /tmp/wt-tmux-test from this project
   3. Yes, and switch to auto mode · auto mode handles these prompts for you
   4. No
 Esc to cancel · Tab to amend
{"─" * 100} wt-tmux-probe ─""".splitlines()

SINGLE = f"""{RULE}
←  ☐ Colour  ☐ Toppings  ✔ Submit  →
Which colour do you pick?
❯ 1. Red
     The colour red.
  2. Green
     The colour green.
  3. Blue
     The colour blue.
  4. Type something.
{RULE}
  5. Chat about this
Enter to select · Tab/Arrow keys to navigate · Esc to cancel""".splitlines()

MULTI = f"""{RULE}
←  ☒ Colour  ☐ Toppings  ✔ Submit  →
Which toppings do you want?
❯ 1. [✔] Cheese
         Add cheese.
  2. [ ] Ham
         Add ham.
  3. [✔] Olives
         Add olives.
  4. [ ] Type something
     Submit
{RULE}
  5. Chat about this
Enter to select · Tab/Arrow keys to navigate · Esc to cancel""".splitlines()

REVIEW = f"""{RULE}
←  ☒ Colour  ☒ Toppings  ✔ Submit  →
Review your answers
 ● Which colour do you pick?
   → Green
 ● Which toppings do you want?
   → Cheese, Olives
Ready to submit your answers?
❯ 1. Submit answers
  2. Cancel""".splitlines()

IDLE = f"""{RULE}
❯
{RULE}
  ⏸ manual mode on · ? for shortcuts""".splitlines()


class Permission(unittest.TestCase):
    def test_reads_the_prompt(self) -> None:
        got = tmux.read_permission(PERMISSION)
        self.assertEqual(got["title"], "Bash command")
        self.assertEqual(got["question"], "Do you want to proceed?")
        self.assertIn("touch probe.txt", got["what"])
        self.assertNotIn("Tip:", got["what"])
        self.assertEqual([o["n"] for o in got["options"]], [1, 2, 3, 4])
        self.assertEqual((got["allow"], got["refuse"]), (1, 4))

    def test_the_fingerprint_moves_with_the_prompt(self) -> None:
        other = [line.replace("touch probe.txt", "rm -rf build") for line in PERMISSION]
        self.assertNotEqual(tmux.read_permission(PERMISSION)["fingerprint"],
                            tmux.read_permission(other)["fingerprint"])

    def test_not_a_permission_prompt(self) -> None:
        for lines in (IDLE, SINGLE, MULTI, REVIEW):
            self.assertIsNone(tmux.read_permission(lines))

    def test_no_refusal_no_offer(self) -> None:
        # A dialog with numbered options but no "No" is not one to press keys at.
        lines = [line for line in PERMISSION if "4. No" not in line]
        self.assertIsNone(tmux.read_permission(lines))


class Question(unittest.TestCase):
    def test_single_choice(self) -> None:
        got = tmux.read_question(SINGLE)
        self.assertEqual(got["question"], "Which colour do you pick?")
        self.assertFalse(got["multiSelect"])
        self.assertEqual([o["label"] for o in got["options"]], ["Red", "Green", "Blue"])
        self.assertEqual(got["options"][1]["description"], "The colour green.")

    def test_multi_select_drops_the_boxes_and_the_extras(self) -> None:
        got = tmux.read_question(MULTI)
        self.assertTrue(got["multiSelect"])
        self.assertEqual([o["label"] for o in got["options"]], ["Cheese", "Ham", "Olives"])

    def test_ticking_a_box_keeps_the_same_question(self) -> None:
        unticked = [line.replace("[✔]", "[ ]") for line in MULTI]
        self.assertEqual(tmux.read_question(MULTI)["fingerprint"],
                         tmux.read_question(unticked)["fingerprint"])

    def test_not_a_question(self) -> None:
        for lines in (IDLE, PERMISSION, REVIEW):
            self.assertIsNone(tmux.read_question(lines))

    def test_review(self) -> None:
        self.assertIn("Cheese, Olives", tmux.read_review(REVIEW))
        self.assertIsNone(tmux.read_review(MULTI))


class Mode(unittest.TestCase):
    def test_reads_each_footer(self) -> None:
        for footer, mode in (
            ("  ⏸ manual mode on · ? for shortcuts                       ◐ medium · /effort", "default"),
            ("  ⏵⏵ accept edits on (shift+tab to cycle)                  ◐ medium · /effort", "acceptEdits"),
            ("  ⏸ plan mode on (shift+tab to cycle)                      ◐ medium · /effort", "plan"),
            ("  ⏵⏵ auto mode on (shift+tab to cycle)                     ◐ medium · /effort", "auto"),
            ("  ⏸ manual mode on · esc to interrupt                      ◐ medium · /effort", "default"),
        ):
            self.assertEqual(tmux.read_mode(IDLE[:-1] + [footer]), mode, footer)

    def test_a_dialog_hides_the_footer(self) -> None:
        for lines in (PERMISSION, SINGLE, MULTI, REVIEW):
            self.assertIsNone(tmux.read_mode(lines))


if __name__ == "__main__":
    unittest.main()
