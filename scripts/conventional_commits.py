#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ALLOWED_TYPES = {
    "feat",
    "fix",
    "perf",
    "docs",
    "ci",
    "test",
    "refactor",
    "chore",
    "release",
}
# GitHub creates this metadata when retaining a real two-parent PR merge.
GITHUB_MERGE_RE = re.compile(r"^Merge pull request #[1-9][0-9]* from \S+$")
SUBJECT_RE = re.compile(r"^(?P<kind>[a-z]+)(?:\([^)]+\))?!?:\s+\S")


def git_subjects(rev_range: str) -> list[str]:
    output = subprocess.check_output(
        ["git", "log", "--pretty=format:%P%x00%s", rev_range], text=True
    ).strip()
    subjects = []
    for line in output.splitlines():
        parents, subject = line.split("\0", 1)
        # Parent evidence is required: an ordinary commit cannot bypass the
        # convention by pretending to be a generated GitHub merge message.
        if len(parents.split()) == 2 and GITHUB_MERGE_RE.fullmatch(subject):
            continue
        if subject.strip():
            subjects.append(subject.strip())
    return subjects


def valid_subject(subject: str) -> bool:
    match = SUBJECT_RE.match(subject)
    return bool(match and match.group("kind") in ALLOWED_TYPES)


def commit_message_subject(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        subject = line.strip()
        if subject and not subject.startswith("#"):
            return subject
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate conventional commit subjects")
    parser.add_argument("subjects", nargs="*")
    parser.add_argument("--range", dest="rev_range")
    parser.add_argument("--message-file")
    args = parser.parse_args()

    subjects = list(args.subjects)
    if args.rev_range:
        subjects.extend(git_subjects(args.rev_range))
    if args.message_file:
        subject = commit_message_subject(Path(args.message_file))
        if subject:
            subjects.append(subject)

    invalid = [subject for subject in subjects if not valid_subject(subject)]
    if invalid:
        print("invalid commit subject(s):")
        for subject in invalid:
            print(f"  {subject}")
        print(
            "commit subjects must use conventional commits because preview notes are generated from them."
        )
        print("example: fix(update): install selected channel")
        print("expected: type(optional-scope): subject")
        print("allowed types: " + ", ".join(sorted(ALLOWED_TYPES)))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
