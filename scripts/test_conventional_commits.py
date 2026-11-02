#!/usr/bin/env python3
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import conventional_commits as commits


class ConventionalCommitHistoryTests(unittest.TestCase):
    github_merge = "Merge pull request #2 from example/fix/desktop"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="herdr-commit-validation-")
        self.repo = Path(self.temp.name)
        self.hooks = self.repo / "empty-hooks"
        self.hooks.mkdir()
        self.git("init", "-b", "master")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.commit("feat: initial")
        self.base = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-c", f"core.hooksPath={self.hooks}", *args],
            cwd=self.repo, text=True, stderr=subprocess.DEVNULL)

    def commit(self, subject):
        self.git("commit", "--allow-empty", "-m", subject)

    @contextmanager
    def in_repository(self):
        previous = Path.cwd()
        os.chdir(self.repo)
        try:
            yield
        finally:
            os.chdir(previous)

    def subjects(self):
        with self.in_repository():
            return commits.git_subjects(f"{self.base}..HEAD")

    def merge(self, subject):
        self.git("switch", "-c", "feature")
        self.commit("fix: authored change")
        self.git("switch", "master")
        self.git("merge", "--no-ff", "-m", subject, "feature")

    def test_github_metadata_on_real_two_parent_merge_is_recognized(self):
        self.merge(self.github_merge)
        self.assertEqual(self.subjects(), ["fix: authored change"])

    def test_github_merge_text_on_ordinary_commit_still_fails(self):
        self.commit(self.github_merge)
        self.assertEqual(self.subjects(), [self.github_merge])
        self.assertFalse(commits.valid_subject(self.subjects()[0]))

    def test_arbitrary_merge_subject_is_not_exempt(self):
        self.merge("unstructured integration message")
        self.assertIn("unstructured integration message", self.subjects())
        self.assertFalse(all(map(commits.valid_subject, self.subjects())))

    def test_invalid_authored_commit_inside_merge_is_not_hidden(self):
        self.git("switch", "-c", "feature")
        self.commit("unstructured authored change")
        self.git("switch", "master")
        self.git("merge", "--no-ff", "-m", self.github_merge, "feature")
        self.assertEqual(self.subjects(), ["unstructured authored change"])
        self.assertFalse(commits.valid_subject(self.subjects()[0]))

    def test_conventional_merge_subject_and_authored_commits_stay_valid(self):
        self.merge("ci: integrate validated changes")
        self.assertEqual(len(self.subjects()), 2)
        self.assertTrue(all(map(commits.valid_subject, self.subjects())))

    def test_literal_subject_validation_does_not_trust_merge_wording(self):
        self.assertFalse(commits.valid_subject(self.github_merge))
        self.assertTrue(commits.valid_subject("fix(input): preserve raw mode"))


if __name__ == "__main__":
    unittest.main()
