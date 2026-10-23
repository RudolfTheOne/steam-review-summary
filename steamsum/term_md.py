"""Streaming Markdown → ANSI for the terminal, styled after glow's "dracula" theme (glamour's palette):
2-space margin, word wrapping, a blank line between blocks, purple headings, orange bold, yellow italics,
green code, "•" bullets; citations [#12] dimmed. Works chunk by chunk, so the answer appears as it is generated."""

import os
import shutil
import sys

MARGIN = "  "
MAX_WIDTH = 160
DRACULA = {"heading": ("#bd93f9", "1"), "bold": ("#ffb86c", "1"), "italic": ("#f1fa8c", "3"),
           "code": ("#50fa7b", ""), "number": ("#8be9fd", ""), "cite": (None, "2")}


def enabled():
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _truecolor():
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def _sgr(hex_color, attrs):
    """SGR parameters for a hex colour: 24-bit if the terminal says so, else the nearest xterm-256 colour."""
    parts = [attrs] if attrs else []
    if hex_color:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        if _truecolor():
            parts.append(f"38;2;{r};{g};{b}")
        else:
            q = lambda v: 0 if v < 48 else 1 if v < 115 else (v - 35) // 40
            parts.append(f"38;5;{16 + 36 * q(r) + 6 * q(g) + q(b)}")
    return ";".join(parts)


STYLES = {k: _sgr(*v) for k, v in DRACULA.items()}


class Renderer:
    def __init__(self, out=None, color=None, width=None):
        self.out = out or sys.stdout
        self.color = enabled() if color is None else color
        self.width = width or min(shutil.get_terminal_size((100, 24)).columns, MAX_WIDTH)
        self.emitted = ""  # ANSI style currently active on the output
        self.prev_block = None  # kind of the previous block, for spacing like glow
        self._reset_line()

    # --- state ---------------------------------------------------------------------------

    def _reset_line(self):
        self.line_start = True
        self.prefix = ""          # undecided characters at the start of a line
        self.heading = False
        self.bold = self.italic = self.code = self.cite = False
        self.star = 0             # pending "*" characters
        self.bracket = False      # "[" seen, waiting to see if a citation "[#" follows
        self.word = []            # (char, style) of the word being built
        self.col = 0
        self.space = False        # a space is pending before the next word

    def _style(self):
        if self.heading:
            return STYLES["heading"]
        for k in ("code", "cite", "bold", "italic"):
            if getattr(self, k):
                return STYLES[k]
        return ""

    # --- output with word wrapping -------------------------------------------------------

    def _emit(self, text, style):
        if self.color and style != self.emitted:
            self.out.write("\033[0m" + (f"\033[{style}m" if style else ""))
            self.emitted = style
        self.out.write(text)

    def _start_line(self, lead="", lead_style=""):
        self._emit(MARGIN, "")
        self._emit(lead, lead_style)
        self.col = len(MARGIN) + len(lead)

    def _block(self, kind):
        """One blank line between blocks, except between items of the same list (glow's spacing)."""
        same_list = kind == self.prev_block and kind in ("item", "number", "quote")
        if self.prev_block is not None and not same_list:
            self._emit("\n", "")
        self.prev_block = kind

    def _flush_word(self):
        if not self.word:
            return
        n = len(self.word)
        if self.col > len(MARGIN) and self.col + int(self.space) + n > self.width:
            self._emit("\n", "")
            self._start_line()
            self.space = False
        if self.space:
            self._emit(" ", "")
            self.col += 1
            self.space = False
        for ch, st in self.word:
            self._emit(ch, st)
        self.col += n
        self.word = []

    def _put(self, ch):
        if ch == " ":
            self._flush_word()
            self.space = True
        else:
            self.word.append((ch, self._style()))

    # --- line start ----------------------------------------------------------------------

    def _line_prefix(self, ch):
        self.prefix += ch
        p = self.prefix.lstrip(" ")
        if p in ("", "#", "##", "###", "####", "-", "*", ">") or (p.rstrip(".)").isdigit() and len(p) < 4):
            return  # not enough to decide yet
        indent = " " * (len(self.prefix) - len(p))
        self.prefix, self.line_start = "", False
        if p.startswith("#") and p.lstrip("#").startswith(" "):
            hashes = p.split(" ", 1)[0]
            self._block("heading")
            self.heading = True
            self._start_line()
            self._text(hashes + " " + p[len(hashes):].lstrip())
        elif p[:2] in ("- ", "* "):
            self._block("item")
            self._start_line(indent + "• ")
            self._text(p[2:])
        elif p.startswith("> "):
            self._block("quote")
            self.italic = True
            self._start_line(indent + "  ")
            self._text(p[2:])
        elif p[0].isdigit() and (". " in p or ") " in p):
            num, rest = p.split(" ", 1)
            self._block("number")
            self._start_line(indent + num + " ", STYLES["number"])
            self._text(rest)
        else:
            self._block("para")
            self._start_line(indent)
            self._text(p)

    # --- inline markup -------------------------------------------------------------------

    def _text(self, s):
        for ch in s:
            self._char(ch)

    def _char(self, ch):
        if self.star and ch != "*":
            if self.star >= 2:
                self.bold = not self.bold
            elif self.italic or ch != " ":  # closing italic, or an opening one followed by text
                self.italic = not self.italic
            else:
                self._put("*")  # a lone "*" between spaces, e.g. "5 * 3"
            self.star = 0
        if self.bracket:
            self.bracket = False
            self.cite = ch == "#"
            self._put("[")
        if ch == "*" and not self.code:
            self.star += 1
        elif ch == "`":
            self.code = not self.code
        elif ch == "[" and not self.code:
            self.bracket = True
        elif ch == "]" and self.cite:
            self._put("]")
            self.cite = False
        else:
            self._put(ch)

    def _newline(self):
        if self.star == 1 and self.italic:
            self.italic = False
        elif self.star >= 2 and self.bold:
            self.bold = False
        if self.bracket:
            self._put("[")
        if self.prefix:
            self._start_line()
            self._text(self.prefix)
        self._flush_word()
        self._emit("\n", "")
        self._reset_line()

    # --- public --------------------------------------------------------------------------

    def feed(self, text):
        for ch in text:
            if ch == "\n" and self.line_start and not self.prefix:
                continue  # the model's own blank lines: spacing is decided by _block
            if ch == "\n":
                self._newline()
            elif self.line_start:
                self._line_prefix(ch)
            else:
                self._char(ch)
        self.out.flush()

    def close(self):
        if self.prefix or not self.line_start:
            self._newline()
        self._emit("", "")
        self.out.flush()
