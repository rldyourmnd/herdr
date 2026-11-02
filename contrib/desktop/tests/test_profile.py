import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_profile import child_environment, load_profile, theme_config, validate_profile

PROFILE = Path(__file__).resolve().parents[1] / "themes/nddev-warm-light.json"


class DesktopContract(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(PROFILE)

    def test_terminal_defaults_match_ui(self):
        self.assertEqual(self.profile["terminal"]["background"],
                         self.profile["ui_colors"]["panel_bg"])
        self.assertEqual(self.profile["terminal"]["foreground"],
                         self.profile["ui_colors"]["text"])

    def test_incomplete_palette_is_rejected(self):
        self.profile["terminal"]["palette"].pop()
        with self.assertRaises(ValueError):
            validate_profile(self.profile)

    def test_transparent_and_system_colors_are_rejected(self):
        for color in ["reset", "transparent", "#ffffff00", "black", None]:
            with self.subTest(color=color):
                data = copy.deepcopy(self.profile)
                data["terminal"]["background"] = color
                with self.assertRaises(ValueError):
                    validate_profile(data)

    def test_different_pane_background_is_rejected(self):
        self.profile["terminal"]["background"] = "#000000"
        with self.assertRaises(ValueError):
            validate_profile(self.profile)

    def test_missing_ui_color_is_rejected(self):
        del self.profile["ui_colors"]["selection_bg"]
        with self.assertRaises(ValueError):
            validate_profile(self.profile)

    def test_inherited_terminal_and_herdr_context_do_not_leak(self):
        env = child_environment({
            "HERDR_SOCKET_PATH": "/foreign/session.sock",
            "HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p1",
            "TERM": "screen", "TERMINFO": "/foreign/terminfo",
            "NO_COLOR": "1", "TMUX": "/foreign/tmux",
            "HOME": "/user", "PATH": "/bin",
            "SSH_AUTH_SOCK": "/owner/agent.sock",
        })
        self.assertFalse(any(k.startswith("HERDR_") for k in env))
        self.assertNotIn("TERMINFO", env)
        self.assertNotIn("NO_COLOR", env)
        self.assertNotIn("TMUX", env)
        self.assertEqual(env["SSH_AUTH_SOCK"], "/owner/agent.sock")
        self.assertEqual(env["TERM"], "xterm-256color")
        self.assertEqual(env["COLORTERM"], "truecolor")

    def test_non_theme_config_survives(self):
        import tomllib
        original = ('onboarding = false\n[theme]\nname = "nord"\n'
                    '[theme.custom]\ntext = "reset"\n'
                    '[terminal]\nnew_cwd = "home"\n'
                    '[ui]\nstatus_indicators = "dots"\n')
        result = tomllib.loads(theme_config(original, self.profile))
        self.assertFalse(result["onboarding"])
        self.assertEqual(result["terminal"], {"new_cwd": "home"})
        self.assertEqual(result["ui"], {"status_indicators": "dots"})
        self.assertEqual(result["theme"]["custom"], self.profile["ui_colors"])

    def test_theme_application_is_idempotent(self):
        once = theme_config("onboarding = false\n", self.profile)
        self.assertEqual(theme_config(once, self.profile), once)


if __name__ == "__main__":
    unittest.main()
