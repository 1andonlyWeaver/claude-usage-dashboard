"""The release workflow builds what packaging/ describes, and releases only from a matching tag."""
import re
from pathlib import Path

import version

REPO = Path(__file__).parent.parent
WORKFLOW = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def step(name):
    """The text of one step, from its name to the next step."""
    return WORKFLOW.split(f"- name: {name}\n", 1)[1].split("- name:", 1)[0]


def test_a_bare_number_tag_triggers_it_and_version_py_fits_that_pattern():
    assert "      - '[0-9]+.[0-9]+.[0-9]+'" in WORKFLOW
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version.__version__)


def test_the_steps_run_in_release_order():
    assert re.findall(r"^      - name: (.+)$", WORKFLOW, re.MULTILINE) == [
        "Read the version",
        "Check the tag matches version.py",
        "Install the packages",
        "Run the tests",
        "Build the app",
        "Smoke-test the build",
        "Build the installer",
        "Install, smoke-test and uninstall",
        "Create a draft release",
    ]


def test_only_a_tag_push_checks_the_tag_and_releases():
    for name in ("Check the tag matches version.py", "Create a draft release"):
        assert "if: github.event_name == 'push'" in step(name), name
    release = step("Create a draft release")
    assert "gh release create" in release and "--draft" in release


def test_the_install_round_trip_checks_that_an_upgrade_keeps_start_at_login_as_it_was():
    round_trip = step("Install, smoke-test and uninstall")
    assert "Remove-ItemProperty $runKey -Name ClaudeUsageDashboard" in round_trip
    assert "The upgrade changed Start at login" in round_trip
    assert "The upgrade turned Start at login back on" in round_trip
    upgrade = round_trip.index("Remove-ItemProperty")
    assert round_trip.index("/TASKS=startatlogin") < round_trip.index("The upgrade changed Start at login") < upgrade
    assert upgrade < round_trip.index("The upgrade turned Start at login back on") < round_trip.index("unins000.exe")


def test_the_job_has_a_time_limit():
    assert "    timeout-minutes: 30\n" in WORKFLOW


def test_it_builds_with_the_files_in_packaging():
    for needle in ("packaging/requirements-build.txt", "packaging/ClaudeUsageDashboard.spec",
                   r"packaging\installer.iss", r"dist\ClaudeUsageDashboard\ClaudeUsageDashboard.exe",
                   "ClaudeUsageDashboard-Setup-", "--smoke", "/TASKS=startatlogin"):
        assert needle in WORKFLOW, needle
