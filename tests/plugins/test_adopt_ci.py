"""adopt-ci.sh: the reviewers team it finds in CODEOWNERS (the function is run on its own, with `gh` stubbed)."""
import os
import pathlib
import shutil
import subprocess
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "adopt-ci.sh"


def team_from(codeowners: str) -> str:
    src = SCRIPT.read_text()
    fn = src[src.index("team_from_codeowners() {"):src.index("\nadopt() {")]
    script = f'''set -euo pipefail
gh() {{ printf '%s' "$CODEOWNERS"; }}
{fn}
team_from_codeowners acme/api || echo NONE
'''
    out = subprocess.run(["bash", "-c", script], env={"CODEOWNERS": codeowners, "PATH": os.environ["PATH"]},
                         capture_output=True, text=True)
    return out.stdout.strip()


@unittest.skipUnless(shutil.which("bash") and shutil.which("sed") and shutil.which("grep"), "needs bash, sed and grep")
class CodeownersTeam(unittest.TestCase):
    def test_a_team_named_only_in_a_comment_is_not_chosen(self):
        self.assertEqual(team_from("*.md @alice # @acme/commented\n"), "NONE")
        self.assertEqual(team_from("# @acme/old\n*.go @acme/maintainers\n"), "@acme/maintainers")

    def test_the_first_team_of_the_organisation_wins_and_only_one_is_printed(self):
        self.assertEqual(team_from("*.go @acme/a @acme/b\n*.md @acme/c\n"), "@acme/a")

    def test_another_organisations_team_is_ignored_and_the_match_is_case_insensitive(self):
        self.assertEqual(team_from("*.go @other/x\n"), "NONE")
        self.assertEqual(team_from("*.go @ACME/Docs\n"), "@ACME/Docs")


if __name__ == "__main__":
    unittest.main()
