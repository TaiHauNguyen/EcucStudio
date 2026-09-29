"""Look & feel modelled on DaVinci Configurator 5 (Eclipse RCP on Windows).

All icons are drawn here as 16x16 pixel maps (no third-party image files).
"""
import tkinter as tk
from tkinter import font as tkfont

COLORS = {
    "bg": "#f0f0f0",            # window background (Eclipse on Windows)
    "view_bg": "#ffffff",
    "tab_bar": "#e8ecf3",
    "tab_active": "#ffffff",
    "tab_inactive": "#dde3ee",
    "tab_border": "#a7b6cf",
    "tab_accent": "#3874d8",
    "section": "#1f5fb4",       # blue section headers ("General Settings")
    "section_line": "#c6d3e6",
    "domain_bg": "#dfe6f1",     # collapsible domain headers in Configuration Editors
    "domain_fg": "#1b1b1b",
    "link": "#0b57d0",
    "label": "#1b1b1b",
    "label_ro": "#8a8a8a",
    "field_ro": "#f3f3f3",
    "error": "#c62828",
    "warning": "#a86400",
    "info": "#1565c0",
    "improvement": "#2e7d32",
    "notinst": "#9e9e9e",
    "readonly": "#757575",
    "user": "#6a1b9a",
    "sel_bg": "#cfe0fb",
    "header_bg": "#eef2f7",
    "status_bg": "#f0f0f0",
    "console_fg": "#1c3fa6",
}

SEVERITY_COLOR = {4: COLORS["error"], 3: COLORS["error"], 2: COLORS["warning"],
                  1: COLORS["improvement"], 0: COLORS["info"]}
SEVERITY_TAG = {4: "error", 3: "error", 2: "warning", 1: "improvement", 0: "info"}

PALETTE = {
    "k": "#3c3c3c", "s": "#8c8c8c", "S": "#cfcfcf", "w": "#ffffff",
    "b": "#2f6fc0", "B": "#9cc3f0", "n": "#1f4e8c",
    "y": "#d49a00", "Y": "#ffdc7a", "o": "#e67e00",
    "g": "#2e8b3a", "G": "#9ad4a6", "r": "#d03020", "R": "#f4a6a0",
    "p": "#7b3fa0", "P": "#dcc2f0", "c": "#1a9aa8", "C": "#a6e3ea",
}

# 16x16 pixel maps; "." = transparent
ICONS = {
    "module": [
        "................",
        "..nnnnnnnnnnnn..",
        "..nBBBBBBBBBBn..",
        "..nBbbbbbbbbBn..",
        "..nBbwwwwwwbBn..",
        "..nBbwbbbbwbBn..",
        "..nBbwbBBbwbBn..",
        "..nBbwbBBbwbBn..",
        "..nBbwbbbbwbBn..",
        "..nBbwwwwwwbBn..",
        "..nBbbbbbbbbBn..",
        "..nBBBBBBBBBBn..",
        "..nnnnnnnnnnnn..",
        "................",
        "................",
        "................"],
    "container": [
        "................",
        "................",
        ".yyyyy..........",
        ".yYYYYy.........",
        ".yYYYYYyyyyyyyy.",
        ".yYYYYYYYYYYYYy.",
        ".yyyyyyyyyyyyyy.",
        ".yYYYYYYYYYYYYy.",
        ".yYYYYYYYYYYYYy.",
        ".yYYYYYYYYYYYYy.",
        ".yYYYYYYYYYYYYy.",
        ".yYYYYYYYYYYYYy.",
        ".yyyyyyyyyyyyyy.",
        "................",
        "................",
        "................"],
    "group": [
        "................",
        "....yyyy........",
        "....yYYYyyyyyyy.",
        "....yYYYYYYYYYy.",
        "..yyyyyyyyyyyYy.",
        "..yYYYYy....yYy.",
        "..yYYYYYyyyyyYy.",
        "..yyyyyyyyyyyyy.",
        "..yYYYYYYYYYYy..",
        "..yYYYYYYYYYYy..",
        "..yYYYYYYYYYYy..",
        "..yYYYYYYYYYYy..",
        "..yyyyyyyyyyyy..",
        "................",
        "................",
        "................"],
    "choice": [
        "................",
        "................",
        ".ppppp..........",
        ".pPPPPp.........",
        ".pPPPPPpppppppp.",
        ".pPPPPPPPPPPPPp.",
        ".pppppppppppppp.",
        ".pPPPPPPPPPPPPp.",
        ".pPPPPPwwPPPPPp.",
        ".pPPPPwPPwPPPPp.",
        ".pPPPPPPwPPPPPp.",
        ".pPPPPPPPPPPPPp.",
        ".pppppppppppppp.",
        "................",
        "................",
        "................"],
    "unknown": [
        "................",
        "................",
        ".sssss..........",
        ".sSSSSs.........",
        ".sSSSSSssssssss.",
        ".sSSSSSSSSSSSSs.",
        ".ssssssssssssss.",
        ".sSSSSSSSSSSSSs.",
        ".sSSSSSSSSSSSSs.",
        ".sSSSSSSSSSSSSs.",
        ".sSSSSSSSSSSSSs.",
        ".sSSSSSSSSSSSSs.",
        ".ssssssssssssss.",
        "................",
        "................",
        "................"],
    "param": [
        "................",
        "................",
        "................",
        "................",
        ".....ssssss.....",
        ".....sBBBBs.....",
        ".....sBbbBs.....",
        ".....sBbbBs.....",
        ".....sBBBBs.....",
        ".....ssssss.....",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "ref": [
        "................",
        "................",
        "................",
        "..........b.....",
        "..........bb....",
        "..bbbbbbbbbbb...",
        "..bbbbbbbbbbbb..",
        "..bbbbbbbbbbb...",
        "..........bb....",
        "..........b.....",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "open": [
        "................",
        "................",
        ".yyyy...........",
        ".yYYYy..........",
        ".yYYYYyyyyyyy...",
        ".yYYYYYYYYYYy...",
        ".yYYyyyyyyyyyyy.",
        ".yYyYYYYYYYYYy..",
        ".yyYYYYYYYYYy...",
        ".yYYYYYYYYYy....",
        ".yyyyyyyyyyy....",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "save": [
        "................",
        ".nnnnnnnnnnnnn..",
        ".nBnwwwwwwwnBn..",
        ".nBnwwwwwwwnBn..",
        ".nBnwwwwwwwnBn..",
        ".nBnnnnnnnnnBn..",
        ".nBBBBBBBBBBBn..",
        ".nBBnnnnnnnBBn..",
        ".nBBnSSSnSnBBn..",
        ".nBBnSSSnSnBBn..",
        ".nBBnSSSnSnBBn..",
        ".nnnnnnnnnnnnn..",
        "................",
        "................",
        "................",
        "................"],
    "undo": [
        "................",
        "................",
        "....b...........",
        "...bb...........",
        "..bbbbbbbbb.....",
        ".bbbbbbbbbbbb...",
        "..bbb.....bbb...",
        "...bb......bb...",
        "....b......bb...",
        "...........bb...",
        "..........bbb...",
        "........bbbb....",
        "................",
        "................",
        "................",
        "................"],
    "redo": [
        "................",
        "................",
        "...........b....",
        "...........bb...",
        ".....bbbbbbbbb..",
        "...bbbbbbbbbbbb.",
        "...bbb.....bbb..",
        "...bb......bb...",
        "...bb......b....",
        "...bb...........",
        "...bbb..........",
        "....bbbb........",
        "................",
        "................",
        "................",
        "................"],
    "back": [
        "................",
        "................",
        "................",
        "......g.........",
        ".....gg.........",
        "....ggggggggg...",
        "...gggggggggg...",
        "....ggggggggg...",
        ".....gg.........",
        "......g.........",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "forward": [
        "................",
        "................",
        "................",
        ".........g......",
        ".........gg.....",
        "...ggggggggg....",
        "...gggggggggg...",
        "...ggggggggg....",
        ".........gg.....",
        ".........g......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "validate": [
        "................",
        "..sssssssss.....",
        "..swwwwwwwss....",
        "..swkkkkwwwss...",
        "..swwwwwwwwws...",
        "..swkkkkkkwws...",
        "..swwwwwwwwws...",
        "..swkkkkkwwws...",
        "..swwwwwwgwws...",
        "..swwwwwggwws...",
        "..swgwwggwwws...",
        "..swggggwwwws...",
        "..swwggwwwwws...",
        "..sssssssssss...",
        "................",
        "................"],
    "generate": [
        "................",
        "......ss........",
        "..ss.ssss.ss....",
        "..sssSSSSsss....",
        "...sSSkkSSs.....",
        ".ssSSk..kSSss...",
        ".ssSk....kSss...",
        "...sSk..kSs.....",
        "..sssSkkSsss....",
        "..ss.sSSs.ss..g.",
        "......ss.....gg.",
        "..........ggggg.",
        "...........ggg..",
        "............g...",
        "................",
        "................"],
    "solve": [
        "................",
        "......yyyy......",
        ".....yYYYYy.....",
        "....yYYwYYYy....",
        "....yYwYYYYy....",
        "....yYYYYYYy....",
        "....yYYYYYYy....",
        ".....yYYYYy.....",
        "......yYYy......",
        "......ssss......",
        "......SSSS......",
        "......ssss......",
        ".......ss.......",
        "................",
        "................",
        "................"],
    "filter": [
        "................",
        "................",
        ".kkkkkkkkkkkkk..",
        "..kSSSSSSSSSk...",
        "...kSSSSSSSk....",
        "....kSSSSSk.....",
        ".....kSSSk......",
        "......kSk.......",
        "......kSk.......",
        "......kSk.......",
        "......kSk.......",
        "......kkk.......",
        "................",
        "................",
        "................",
        "................"],
    "find": [
        "................",
        "...sssss........",
        "..sBBBBBs.......",
        ".sBwwBBBBs......",
        ".sBwBBBBBs......",
        ".sBBBBBBBs......",
        ".sBBBBBBBs......",
        "..sBBBBBs.......",
        "...sssssss......",
        ".........kk.....",
        "..........kk....",
        "...........kk...",
        "............kk..",
        "................",
        "................",
        "................"],
    "console": [
        "................",
        ".nnnnnnnnnnnnnn.",
        ".nBBBBBBBBBBBBn.",
        ".nkkkkkkkkkkkkn.",
        ".nkgkkkkkkkkkkn.",
        ".nkkgkkkkkkkkkn.",
        ".nkgkkggggkkkkn.",
        ".nkkkkkkkkkkkkn.",
        ".nkkkkkkkkkkkkn.",
        ".nnnnnnnnnnnnnn.",
        "......ssss......",
        "....ssssssss....",
        "................",
        "................",
        "................",
        "................"],
    "properties": [
        "................",
        "..sssssssssss...",
        "..swwwwwwwwws...",
        "..swbbwkkkkws...",
        "..swwwwwwwwws...",
        "..swbbwkkkkws...",
        "..swwwwwwwwws...",
        "..swbbwkkkkws...",
        "..swwwwwwwwws...",
        "..swbbwkkkkws...",
        "..swwwwwwwwws...",
        "..sssssssssss...",
        "................",
        "................",
        "................",
        "................"],
    "editors": [
        "................",
        ".......o........",
        "...o...o...o....",
        "....o..o..o.....",
        ".....ooooo......",
        "....ooYYYoo.....",
        ".ooooYYYYYoooo..",
        "....ooYYYoo.....",
        ".....ooooo......",
        "....o..o..o.....",
        "...o...o...o....",
        ".......o........",
        "................",
        "................",
        "................",
        "................"],
    "basic": [
        "................",
        ".nnnnnnnnnnnnn..",
        ".nBBBBBBBBBBBn..",
        ".nnnnnnnnnnnnn..",
        ".nwwwnwwwwwwwn..",
        ".nwyynwkkkkwwn..",
        ".nwwwnwwwwwwwn..",
        ".nwyynwkkkwwwn..",
        ".nwwwnwwwwwwwn..",
        ".nwyynwkkkkkwn..",
        ".nwwwnwwwwwwwn..",
        ".nnnnnnnnnnnnn..",
        "................",
        "................",
        "................",
        "................"],
    "genresult": [
        "................",
        "..sssssssss.....",
        "..swwwwwwwss....",
        "..swkkkkwwwss...",
        "..swwwwwwwwws...",
        "..swkkkkkkwws...",
        "..swwwwwwwwws...",
        "..swkkkkkkwws...",
        "..swwwwwsssss...",
        "..swwwwsggggs...",
        "..swwwwsgwwgs...",
        "..swwwwsgwwgs...",
        "..sssssssssss...",
        "................",
        "................",
        "................"],
    "settings": [
        "................",
        "......ss........",
        "..ss.ssss.ss....",
        "..sssSSSSsss....",
        "...sSSkkSSs.....",
        ".ssSSk..kSSss...",
        ".ssSk....kSss...",
        "...sSk..kSs.....",
        "..sssSkkSsss....",
        "..ss.sSSs.ss....",
        "......ss........",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "add": [
        "................",
        "................",
        "................",
        "......ggg.......",
        "......gGg.......",
        "......gGg.......",
        "...ggggGgggg....",
        "...gGGGGGGGg....",
        "...ggggGgggg....",
        "......gGg.......",
        "......gGg.......",
        "......ggg.......",
        "................",
        "................",
        "................",
        "................"],
    "delete": [
        "................",
        "................",
        "................",
        "...rr....rr.....",
        "....rr..rr......",
        ".....rrrr.......",
        "......rr........",
        ".....rrrr.......",
        "....rr..rr......",
        "...rr....rr.....",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "user": [
        "................",
        "......ppp.......",
        ".....pPPPp......",
        ".....pPPPp......",
        ".....pPPPp......",
        "......ppp.......",
        "....ppppppp.....",
        "...pPPPPPPPp....",
        "...pPPPPPPPp....",
        "...ppppppppp....",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "lock": [
        "................",
        "......sss.......",
        ".....s...s......",
        ".....s...s......",
        "....yyyyyyy.....",
        "....yYYYYYy.....",
        "....yYYkYYy.....",
        "....yYYkYYy.....",
        "....yYYYYYy.....",
        "....yyyyyyy.....",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "derived": [
        "................",
        "................",
        "..ccc...........",
        ".c...c..........",
        ".c...cccc.......",
        ".c...c...c......",
        "..ccc....c......",
        "......c..c......",
        ".......ccc......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "copy": [
        "................",
        ".sssssss........",
        ".swwwwws........",
        ".swsssssssss....",
        ".swswwwwwwws....",
        ".swswkkkkkws....",
        ".swswwwwwwws....",
        ".sssswkkkkws....",
        "....swwwwwws....",
        "....sssssssss...",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "collapse": [
        "................",
        "................",
        "................",
        "...k.....k......",
        "..kk....kk......",
        ".kk....kk.......",
        "kk....kk........",
        ".kk....kk.......",
        "..kk....kk......",
        "...k.....k......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
    "tree": [
        "................",
        ".nnnnnnnnnnnnn..",
        ".nBBBBBBBBBBBn..",
        ".nnnnnnnnnnnnn..",
        ".nwwwwnwwwwwwn..",
        ".nwkwwnwwwwwwn..",
        ".nwwkwnwwwwwwn..",
        ".nwkwwnwwwwwwn..",
        ".nwwwwnwwwwwwn..",
        ".nwkwwnwwwwwwn..",
        ".nwwwwnwwwwwwn..",
        ".nnnnnnnnnnnnn..",
        "................",
        "................",
        "................",
        "................"],
    "phase": [
        "................",
        "................",
        "....bbbbbbb.....",
        "...bBBBBBBBb....",
        "..bBBwwwwwBBb...",
        "..bBwBBBBBwBb...",
        "..bBwBBBBBwBb...",
        "..bBBwwwwwBBb...",
        "...bBBBBBBBb....",
        "....bbbbbbb.....",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................"],
}

# glyphs drawn onto a round badge (5x5, centred)
_GLYPH = {
    "x": ["w...w", ".w.w.", "..w..", ".w.w.", "w...w"],
    "!": ["..w..", "..w..", "..w..", ".....", "..w.."],
    "i": ["..w..", ".....", "..w..", "..w..", "..w.."],
    "v": ["....w", "...w.", "w.w..", ".w...", "....."],
    "+": ["..w..", "..w..", "wwwww", "..w..", "..w.."],
}


def _from_map(rows):
    img = tk.PhotoImage(width=16, height=16)
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            ch = row[x]
            if ch == ".":
                x += 1
                continue
            x2 = x
            while x2 < len(row) and row[x2] == ch:
                x2 += 1
            img.put(PALETTE[ch], to=(x, y, x2, y + 1))
            x = x2
    return img


def _badge(color, glyph, triangle=False):
    img = tk.PhotoImage(width=16, height=16)
    for y in range(16):
        for x in range(16):
            if triangle:
                inside = y >= 2 and y <= 13 and abs(x - 7.5) <= (y - 1.5) * 0.62
            else:
                inside = (x - 7.5) ** 2 + (y - 7.5) ** 2 <= 36
            if inside:
                img.put(color, (x, y))
    g = _GLYPH[glyph]
    oy = 7 if triangle else 5
    for gy, row in enumerate(g):
        for gx, ch in enumerate(row):
            if ch == "w":
                img.put("#ffffff" if not triangle else "#3c3c3c", (5 + gx + 1, oy + gy))
    return img


class Icons:
    """Created after the Tk root exists (PhotoImage needs an interpreter)."""

    def __init__(self):
        for name, rows in ICONS.items():
            setattr(self, name, _from_map(rows))
        self.error = _badge("#d8342a", "x")
        self.warning = _badge("#f2c230", "!", triangle=True)
        self.info = _badge("#2f79d1", "i")
        self.improvement = _badge("#3a9b4a", "+")
        self.ok = _badge("#3a9b4a", "v")
        self.bulb = self.solve
        self.blank = tk.PhotoImage(width=16, height=16)

    def severity(self, sev):
        return {4: self.error, 3: self.error, 2: self.warning, 1: self.improvement, 0: self.info}.get(int(sev))


def init_style(root):
    from tkinter import ttk
    style = ttk.Style(root)
    for theme in ("vista", "winnative", "clam"):
        try:
            style.theme_use(theme)
            break
        except tk.TclError:
            continue
    base = tkfont.nametofont("TkDefaultFont")
    base.configure(family="Segoe UI", size=9)
    for f in ("TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(f).configure(family="Segoe UI", size=9)
        except tk.TclError:
            pass
    root.configure(background=COLORS["bg"])
    style.configure("Treeview", rowheight=19, background="#ffffff", fieldbackground="#ffffff")
    style.configure("Treeview.Heading", font=("Segoe UI", 9))
    style.map("Treeview", background=[("selected", COLORS["sel_bg"])], foreground=[("selected", "#000000")])
    style.configure("View.TFrame", background=COLORS["view_bg"])
    style.configure("Bg.TFrame", background=COLORS["bg"])
    style.configure("Form.TFrame", background=COLORS["view_bg"])
    style.configure("Form.TLabel", background=COLORS["view_bg"], foreground=COLORS["label"])
    style.configure("FormRO.TLabel", background=COLORS["view_bg"], foreground=COLORS["label_ro"])
    style.configure("Section.TLabel", background=COLORS["view_bg"], foreground=COLORS["section"],
                    font=("Segoe UI", 9, "bold"))
    style.configure("Hint.TLabel", background=COLORS["view_bg"], foreground="#8a8a8a", font=("Segoe UI", 8))
    style.configure("Title.TLabel", background=COLORS["view_bg"], foreground=COLORS["section"],
                    font=("Segoe UI", 9, "bold"))
    style.configure("Link.TLabel", background=COLORS["view_bg"], foreground=COLORS["link"],
                    font=("Segoe UI", 9, "underline"))
    style.configure("Crumb.TLabel", background=COLORS["view_bg"], foreground=COLORS["link"],
                    font=("Segoe UI", 9, "underline"))
    style.configure("CrumbBold.TLabel", background=COLORS["view_bg"], foreground="#1b1b1b",
                    font=("Segoe UI", 9, "bold"))
    style.configure("Status.TLabel", background=COLORS["status_bg"], padding=(4, 1))
    style.configure("Form.TCheckbutton", background=COLORS["view_bg"])
    style.configure("Header.TLabel", font=("Segoe UI", 10, "bold"))
    return style
