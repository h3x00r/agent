import io
import unittest

from agentctl.ui import style, table, text


class Clean(unittest.TestCase):
    def test_control_bytes_go(self):
        self.assertEqual(text.clean("a\x1b]0;x\x07b\tc\nd\x7fe"), "a]0;xbcde")

    def test_c1_goes_byte_wise(self):
        self.assertEqual(text.clean("a\u009d0;c1\u009cb"), "a0;c1b")

    def test_a_lone_byte_stays(self):
        self.assertEqual(text.clean("a\udc9db"), "a\udc9db")   # 0x9d alone, not after 0xc2

    def test_empty_is_a_dash(self):
        self.assertEqual(text.clean(""), "-")
        self.assertEqual(text.clean("\x01\x02"), "-")


class Width(unittest.TestCase):
    def test_colours_take_no_room(self):
        self.assertEqual(text.width("\033[32mrunning\033[0m", utf8=True), 7)

    def test_characters_or_bytes(self):
        self.assertEqual(text.width("é", utf8=True), 1)
        self.assertEqual(text.width("é", utf8=False), 2)

    def test_locale(self):
        self.assertTrue(text.utf8_locale({"LANG": "C.UTF-8"}))
        self.assertFalse(text.utf8_locale({"LC_ALL": "C", "LANG": "C.UTF-8"}))
        self.assertFalse(text.utf8_locale({}))


class Table(unittest.TestCase):
    def test_columns(self):
        p = style.Palette(False)
        rows = [["NAME", "N", "DIR"], ["a", "10", "/x"], ["bbb", "2", "/yy"]]
        self.assertEqual(table.render("lrl", rows, p, utf8=True),
                         ["NAME   N  DIR", "a     10  /x", "bbb    2  /yy"])

    def test_header_dim_and_colours_unmeasured(self):
        p = style.Palette(True)
        rows = [["A", "B"], [p.green + "up" + p.reset, "x"]]
        self.assertEqual(table.render("ll", rows, p, utf8=True),
                         ["\033[90mA   B\033[0m", "\033[32mup\033[0m  x"])

    def test_an_empty_cell_disappears(self):
        p = style.Palette(False)
        self.assertEqual(table.render("lll", [["a", "", "c"]], p, utf8=True), ["a  c"])


class Palette(unittest.TestCase):
    def test_off_without_a_terminal_or_with_no_color(self):
        self.assertFalse(style.Palette.for_stdout(io.StringIO(), {}).enabled)

        class Tty(io.StringIO):
            def isatty(self):
                return True
        self.assertTrue(style.Palette.for_stdout(Tty(), {}).enabled)
        self.assertFalse(style.Palette.for_stdout(Tty(), {"NO_COLOR": "1"}).enabled)
        self.assertTrue(style.Palette.for_stdout(Tty(), {"NO_COLOR": ""}).enabled)

    def test_meanings(self):
        p = style.Palette(True)
        self.assertEqual(p.state("healthy"), p.green)
        self.assertEqual(p.state("unhealthy"), p.red)
        self.assertEqual(p.state("starting"), p.yellow)
        self.assertEqual(p.state("stopped"), p.dim)
        self.assertEqual(p.load("80"), p.red)
        self.assertEqual(p.load("50"), p.yellow)
        self.assertEqual(p.load("49"), p.green)
        self.assertEqual(p.load("-"), "")


if __name__ == "__main__":
    unittest.main()
