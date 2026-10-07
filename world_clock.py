#!/usr/bin/env python3
"""World Clock — Geochron-style map with day/night blend and city-lights at night."""

import tkinter as tk
import datetime
import math
import threading
import urllib.request
import urllib.parse
import json
import io
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageTk

# ── Asset paths (baked into Docker image) ──────────────────────────────────
DAY_MAP_PATH   = "/opt/worldclock/assets/world_map_day.png"
NIGHT_MAP_PATH = "/opt/worldclock/assets/world_map_night.png"

# ── Palette ────────────────────────────────────────────────────────────────
BG         = "#0d1117"
HEADER_BG  = "#010409"
STRIP_BG   = "#010409"
CARD_BG    = "#161b22"
FLYOUT_BG  = "#1c2128"
SEP_CLR    = "#21262d"
BORDER_CLR = "#30363d"
CITY_FG    = "#e6edf3"
TIME_FG    = "#58a6ff"
DATE_FG    = "#8b949e"
TZ_FG      = "#3fb950"

# ── Configurable strip behaviour ────────────────────────────────────────────
STRIP_VISIBLE     = 10    # city tiles shown at once in the bottom strip
STRIP_HOVER_SCALE = 1.25  # scale factor for CityColumnOverlay font sizes
STRIP_DAY_ICON    = "☀"        # shown when local hour is within STRIP_DAY_HOURS
STRIP_NIGHT_ICON  = "🌗"       # shown outside STRIP_DAY_HOURS
STRIP_DAY_HOURS   = range(6, 20) # 06:00–19:59 inclusive
STRIP_DAY_FG      = "#ffcc00"    # yellow sun
STRIP_NIGHT_FG    = "#8b949e"    # gray moon
STRIP_DAY_TIME_FG   = "#ffcc00"  # time label colour during the day
STRIP_NIGHT_TIME_FG = "#90caf9"  # time label colour at night

# ── Cities: (name, tz, lat°N, lon°E) ───────────────────────────────────────
CITIES = [
    ("New York",     "America/New_York",                 40.71,  -74.01),
    ("Los Angeles",  "America/Los_Angeles",              34.05, -118.24),
    ("São Paulo",    "America/Sao_Paulo",               -23.55,  -46.63),
    ("Buenos Aires", "America/Argentina/Buenos_Aires",  -34.60,  -58.38),
    ("London",       "Europe/London",                    51.51,   -0.13),
    ("Berlin",       "Europe/Berlin",                    52.52,   13.41),
    ("Moscow",       "Europe/Moscow",                    55.75,   37.62),
    ("Cairo",        "Africa/Cairo",                     30.04,   31.24),
    ("Lagos",        "Africa/Lagos",                      6.45,    3.39),
    ("Nairobi",      "Africa/Nairobi",                   -1.29,   36.82),
    ("Cape Town",    "Africa/Johannesburg",             -33.93,   18.42),
    ("Dubai",        "Asia/Dubai",                       25.20,   55.27),
    ("Mumbai",       "Asia/Kolkata",                     19.08,   72.88),
    ("Bangkok",      "Asia/Bangkok",                     13.75,  100.52),
    ("Singapore",    "Asia/Singapore",                    1.35,  103.82),
    ("Hong Kong",    "Asia/Hong_Kong",                   22.32,  114.17),
    ("Tokyo",        "Asia/Tokyo",                       35.68,  139.69),
    ("Seoul",        "Asia/Seoul",                       37.57,  126.98),
    ("Sydney",       "Australia/Sydney",                -33.87,  151.21),
    ("Auckland",     "Pacific/Auckland",                -36.86,  174.77),
]

# Wikipedia article titles that differ from the city display name
_WIKI_TITLES = {
    "New York":     "New_York_City",
    "Los Angeles":  "Los_Angeles",
    "São Paulo":    "São_Paulo",
    "Buenos Aires": "Buenos_Aires",
    "Hong Kong":    "Hong_Kong",
}


# ── Solar position (Spencer 1971, pure stdlib) ──────────────────────────────

def _solar_decl_eot(dt_utc: datetime.datetime):
    B    = math.radians((360 / 365) * (dt_utc.timetuple().tm_yday - 81))
    decl = math.radians(23.45) * math.sin(B)
    eot  = 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
    return decl, eot


def _subsolar_lon(dt_utc: datetime.datetime, eot_min: float) -> float:
    ut  = dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600
    lon = -(ut - 12) * 15 - (eot_min / 60) * 15
    return (lon + 180) % 360 - 180


def _elevation_lat(lon_deg, sub_lon_deg, decl_rad, elev_deg=0.0):
    H     = math.radians(lon_deg - sub_lon_deg)
    a     = math.sin(decl_rad)
    b     = math.cos(decl_rad) * math.cos(H)
    c     = math.sin(math.radians(elev_deg))
    R     = math.hypot(a, b)
    if R < 1e-9:
        return 90.0 if c >= 0 else -90.0
    ratio = c / R
    if ratio > 1.0:
        return 90.0
    if ratio < -1.0:
        return -90.0 if a >= 0 else 90.0
    lat = math.asin(ratio) - math.atan2(b, a)
    if a < 0:
        # Sun south of the equator: the other asin branch is the valid one
        lat = math.pi - math.asin(ratio) - math.atan2(b, a)
        lat = (lat + math.pi) % (2 * math.pi) - math.pi
    return math.degrees(lat)


def _fmt_utc_offset(dt: datetime.datetime) -> str:
    off = dt.utcoffset()
    if off is None:
        return "UTC"
    s    = int(off.total_seconds())
    sign = "+" if s >= 0 else "-"
    s    = abs(s)
    h, m = divmod(s // 60, 60)
    return f"UTC{sign}{h:02d}:{m:02d}"


# ── Day/night blend mask ────────────────────────────────────────────────────

def _blend_mask(map_w, map_h, decl_rad, sub_lon):
    mask  = Image.new("L", (map_w, map_h), 255)
    draw  = ImageDraw.Draw(mask)
    north = decl_rad > 0

    for px in range(map_w):
        lon    = (px / map_w) * 360 - 180
        t0_lat = _elevation_lat(lon, sub_lon, decl_rad, 0.0)
        t6_lat = _elevation_lat(lon, sub_lon, decl_rad, -6.0)

        def to_py(lat):
            return max(0, min(map_h - 1, int((90 - lat) / 180 * map_h)))

        t0_py = to_py(t0_lat)
        t6_py = to_py(t6_lat)

        if north:
            if t6_py < map_h:
                draw.rectangle([px, t6_py, px, map_h - 1], fill=0)
            band = max(1, t6_py - t0_py)
            for dy in range(band):
                py = t0_py + dy
                if 0 <= py < map_h:
                    mask.putpixel((px, py), int(255 * (1 - dy / band)))
        else:
            if t6_py > 0:
                draw.rectangle([px, 0, px, t6_py], fill=0)
            band = max(1, t0_py - t6_py)
            for dy in range(band):
                py = t6_py + dy
                if 0 <= py < map_h:
                    mask.putpixel((px, py), int(255 * dy / band))

    return mask


# ── Wikipedia helpers ───────────────────────────────────────────────────────

def _fetch_wiki(city_name: str) -> tuple[str, str, str]:
    title = _WIKI_TITLES.get(city_name, city_name.replace(" ", "_"))
    title = urllib.parse.quote(title)
    url   = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
    try:
        req  = urllib.request.Request(url,
                   headers={"User-Agent": "GeochronWorldClock/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data    = json.loads(resp.read().decode())
            desc    = data.get("description", "")
            extract = data.get("extract", "")
            thumb   = (data.get("thumbnail") or {}).get("source", "")
            sentences = extract.split(". ")
            short = ". ".join(sentences[:3])
            if len(short) > 300:
                short = short[:300].rsplit(" ", 1)[0] + " …"
            elif short and not short.endswith("."):
                short += "."
            return desc, short, thumb
    except Exception:
        return "", "", ""


def _download_image(url: str, max_w=96, max_h=72):
    if not url:
        return None
    try:
        req = urllib.request.Request(url,
                  headers={"User-Agent": "GeochronWorldClock/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((max_w, max_h), Image.LANCZOS)
        return img
    except Exception:
        return None


# ── City info flyout ────────────────────────────────────────────────────────
# Child of the root window so it can appear anywhere (map or above strip).

class CityFlyout(tk.Frame):
    """
    Floating info panel showing Wikipedia content for a city.
    Positioned in root-window coordinates so it can be triggered from
    both map clicks and strip-column hovers.
    """

    def __init__(self, app: tk.Tk):
        super().__init__(app,
                         bg=FLYOUT_BG,
                         highlightbackground=TIME_FG,
                         highlightthickness=1,
                         padx=12, pady=10)
        self._app        = app
        self._close_job  = None
        self._close_delay = 1000
        self._city_tz    = None
        self._city_photo = None

        # Top row: info (left) + thumbnail (right)
        top = tk.Frame(self, bg=FLYOUT_BG)
        top.pack(fill=tk.X)

        self._img_lbl = tk.Label(top, bg=FLYOUT_BG, bd=0, anchor="ne")
        self._img_lbl.pack(side=tk.RIGHT, anchor="ne", padx=(8, 0))

        info = tk.Frame(top, bg=FLYOUT_BG)
        info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._name_lbl = tk.Label(info, bg=FLYOUT_BG, fg=CITY_FG,
                                  font=("Sans", 13, "bold"), anchor="w")
        self._name_lbl.pack(fill=tk.X)

        self._time_lbl = tk.Label(info, bg=FLYOUT_BG, fg=TIME_FG,
                                  font=("Monospace", 12), anchor="w")
        self._time_lbl.pack(fill=tk.X)

        self._tz_lbl = tk.Label(info, bg=FLYOUT_BG, fg=TZ_FG,
                                font=("Sans", 9), anchor="w")
        self._tz_lbl.pack(fill=tk.X, pady=(0, 6))

        tk.Frame(self, bg=BORDER_CLR, height=1).pack(fill=tk.X, pady=(0, 6))

        self._desc_lbl = tk.Label(self, bg=FLYOUT_BG, fg=DATE_FG,
                                  font=("Sans", 9, "italic"),
                                  anchor="w", justify=tk.LEFT, wraplength=290)
        self._desc_lbl.pack(fill=tk.X)

        self._extract_lbl = tk.Label(self, bg=FLYOUT_BG, fg="#c9d1d9",
                                     font=("Sans", 9),
                                     anchor="w", justify=tk.LEFT, wraplength=290)
        self._extract_lbl.pack(fill=tk.X, pady=(4, 0))

        self._bind_hover(self)
        self.place_forget()

    # ── Public API ──────────────────────────────────────────────────────────

    def show(self, rx: int, ry: int, name: str, tz: ZoneInfo,
             close_delay: int = 1000):
        """
        rx, ry  – hint position in root-window coordinates.
        The flyout positions itself near (rx, ry), avoiding screen edges.
        close_delay – ms before auto-close once mouse leaves.
        """
        self._cancel_close()
        self._close_delay = close_delay
        self._city_tz     = tz
        self._name_lbl.config(text=name)
        self._refresh_time()
        self._desc_lbl.config(text="")
        self._extract_lbl.config(text="Fetching…")
        self._img_lbl.config(image="")

        ww, wh = self._app.winfo_width(), self._app.winfo_height()
        fw = 320

        fx = rx + 14
        if fx + fw > ww - 8:
            fx = rx - fw - 14
        fx = max(0, fx)

        # Prefer above the hint point; shift up if near bottom
        fy = ry - 20
        if fy + 230 > wh:
            fy = max(8, ry - 240)

        self.place(x=fx, y=fy, width=fw)
        self.lift()

        threading.Thread(target=self._bg_fetch, args=(name,),
                         daemon=True).start()

    def refresh_time(self):
        if self.winfo_ismapped() and self._city_tz:
            self._refresh_time()

    def close(self):
        self.place_forget()
        self._close_job  = None
        self._city_tz    = None
        self._city_photo = None
        self._img_lbl.config(image="")

    # ── Internals ───────────────────────────────────────────────────────────

    def _refresh_time(self):
        now = datetime.datetime.now(self._city_tz)
        self._time_lbl.config(text=now.strftime("%H:%M:%S"))
        self._tz_lbl.config(
            text=f"{now.strftime('%Z')}  ·  {_fmt_utc_offset(now)}")

    def _bg_fetch(self, city_name):
        desc, extract, thumb_url = _fetch_wiki(city_name)
        img = _download_image(thumb_url)
        self._app.after(0, lambda: self._apply(desc, extract, img))

    def _apply(self, desc, extract, img):
        if not self.winfo_ismapped():
            return
        self._desc_lbl.config(text=desc)
        self._extract_lbl.config(text=extract or "No information available.")
        if img is not None:
            self._city_photo = ImageTk.PhotoImage(img)
            self._img_lbl.config(image=self._city_photo)
        else:
            self._city_photo = None
            self._img_lbl.config(image="")

    def _bind_hover(self, widget):
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        for child in widget.winfo_children():
            self._bind_hover(child)

    def _is_inside(self, widget) -> bool:
        w = widget
        while w is not None:
            if w is self:
                return True
            w = getattr(w, "master", None)
        return False

    def _on_enter(self, _event):
        self._cancel_close()

    def _on_leave(self, event):
        if not self._is_inside(self.winfo_containing(event.x_root, event.y_root)):
            self._schedule_close()

    def _schedule_close(self):
        self._cancel_close()
        self._close_job = self._app.after(self._close_delay, self.close)

    def _cancel_close(self):
        if self._close_job:
            self._app.after_cancel(self._close_job)
            self._close_job = None


# ── Geochron canvas ─────────────────────────────────────────────────────────

class GeochronCanvas(tk.Canvas):

    _CLICK_RADIUS = 10

    def __init__(self, parent, cities, flyout: CityFlyout):
        super().__init__(parent, bg=BG, highlightthickness=0)
        self._cities    = [(n, ZoneInfo(tz), lat, lon)
                           for n, tz, lat, lon in cities]
        self._day_img   = Image.open(DAY_MAP_PATH).convert("RGB")
        self._night_img = Image.open(NIGHT_MAP_PATH).convert("RGB")
        if self._night_img.size != self._day_img.size:
            self._night_img = self._night_img.resize(
                self._day_img.size, Image.LANCZOS)
        self._map_w, self._map_h = self._day_img.size
        self._photo     = None
        self._last_min  = -1
        self._dw = self._dh = 1
        self._city_positions: list[tuple[int, int]] = []
        self._flyout = flyout

        self.bind("<Configure>", self._on_resize)
        self.bind("<Button-1>",  self._on_click)

    def _on_resize(self, event):
        self._dw, self._dh = event.width, event.height
        self.refresh(force=True)

    def refresh(self, utc_now=None, force=False):
        if utc_now is None:
            utc_now = datetime.datetime.now(datetime.timezone.utc)
        if self._dw < 2 or self._dh < 2:
            return
        if force or utc_now.minute != self._last_min:
            self._redraw_map(utc_now)
            self._last_min = utc_now.minute
        else:
            self._draw_cities(utc_now)

    def _redraw_map(self, utc_now):
        decl, eot  = _solar_decl_eot(utc_now)
        sub_lon    = _subsolar_lon(utc_now, eot)
        mask       = _blend_mask(self._map_w, self._map_h, decl, sub_lon)
        composite  = Image.composite(self._day_img, self._night_img, mask)
        scaled     = composite.resize((self._dw, self._dh), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(scaled)
        self.delete("map")
        self.create_image(0, 0, anchor="nw", image=self._photo, tags="map")
        self.tag_lower("map")
        self._draw_cities(utc_now)
        self._draw_subsolar(sub_lon, decl)

    def _latlon_to_xy(self, lat, lon):
        return (int((lon + 180) / 360 * self._dw),
                int((90 - lat)  / 180 * self._dh))

    def _draw_cities(self, utc_now):
        self.delete("cities")
        positions = []
        for name, tz, lat, lon in self._cities:
            x, y     = self._latlon_to_xy(lat, lon)
            positions.append((x, y))
            now      = datetime.datetime.now(tz)
            daytime   = 6 <= now.hour < 20
            dot_fill  = "#ffcc00" if daytime else "#4a9fd4"
            time_fill = "#ffcc00" if daytime else "#90caf9"
            r = 3
            self.create_oval(x-r, y-r, x+r, y+r,
                             fill=dot_fill, outline="#ffffff",
                             width=1, tags="cities")
            self.create_text(x, y-6, text=name,
                             fill="#ffffff", font=("Sans", 11, "bold"),
                             anchor="s", tags="cities")
            self.create_text(x, y+6, text=now.strftime("%H:%M"),
                             fill=time_fill, font=("Monospace", 11),
                             anchor="n", tags="cities")
        self._city_positions = positions

    def _draw_subsolar(self, sub_lon, decl_rad):
        self.delete("sun")
        x, y = self._latlon_to_xy(math.degrees(decl_rad), sub_lon)
        r = 6
        self.create_oval(x-r, y-r, x+r, y+r,
                         fill="#ffdd00", outline="#ffffff", width=2, tags="sun")
        self.create_line(x-12, y, x+12, y, fill="#ffdd00", width=1, tags="sun")
        self.create_line(x, y-12, x, y+12, fill="#ffdd00", width=1, tags="sun")

    def _on_click(self, event):
        best_d, best_i = float("inf"), -1
        for i, (cx, cy) in enumerate(self._city_positions):
            d = math.hypot(event.x - cx, event.y - cy)
            if d < best_d:
                best_d, best_i = d, i
        if best_i >= 0 and best_d <= self._CLICK_RADIUS:
            name, tz, *_ = self._cities[best_i]
            cx, cy = self._city_positions[best_i]
            # Convert canvas-relative to root-window-relative
            app = self._flyout._app
            rx  = cx + self.winfo_rootx() - app.winfo_rootx()
            ry  = cy + self.winfo_rooty() - app.winfo_rooty()
            self._flyout.show(rx, ry, name, tz, close_delay=1000)


# ── City column tile ─────────────────────────────────────────────────────────

class CityColumn(tk.Frame):
    """
    Single city tile in the bottom strip.
    Hovering shows a scaled-up overlay immediately and the shared CityFlyout
    after a 500 ms delay; moving away hides the overlay and closes the flyout
    after a 500 ms delay.
    """
    _ICON_SIZE   = 20
    _NAME_SIZE   = 9
    _TIME_SIZE   = 11
    _OPEN_DELAY  = 500   # ms before flyout opens
    _CLOSE_DELAY = 500   # ms before flyout closes

    def __init__(self, parent, name: str, t_var: tk.StringVar,
                 tz: ZoneInfo, flyout: "CityFlyout", overlay: "CityColumnOverlay",
                 icon: str = STRIP_DAY_ICON, icon_fg: str = STRIP_DAY_FG,
                 time_fg: str = STRIP_DAY_TIME_FG):
        super().__init__(parent, bg=CARD_BG, padx=6, pady=3)
        self._name    = name
        self._t_var   = t_var
        self._tz      = tz
        self._flyout  = flyout
        self._overlay = overlay
        self._open_job  = None
        self._close_job = None

        self._icon_lbl = tk.Label(self, text=icon, bg=CARD_BG, fg=icon_fg,
                                  font=("Sans", self._ICON_SIZE))
        self._icon_lbl.pack()
        tk.Label(self, text=name, bg=CARD_BG, fg=DATE_FG,
                 font=("Sans", self._NAME_SIZE)).pack()
        self._time_lbl = tk.Label(self, textvariable=t_var, bg=CARD_BG, fg=time_fg,
                                  font=("Monospace", self._TIME_SIZE, "bold"))
        self._time_lbl.pack()

        for w in (self, *self.winfo_children()):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

    def update_icon(self, icon: str, icon_fg: str, time_fg: str):
        self._icon_lbl.config(text=icon, fg=icon_fg)
        self._time_lbl.config(fg=time_fg)

    def _on_enter(self, _event):
        if self._close_job:
            self.after_cancel(self._close_job)
            self._close_job = None
        self._flyout._cancel_close()
        self._overlay.show_over(self)
        if self._open_job is None:
            self._open_job = self.after(self._OPEN_DELAY, self._open_flyout)

    def _on_leave(self, event):
        under = self.winfo_containing(event.x_root, event.y_root)
        w = under
        while w is not None:
            if w is self._flyout or w is self or w is self._overlay:
                return
            w = getattr(w, "master", None)
        self._overlay.hide()
        if self._open_job:
            self.after_cancel(self._open_job)
            self._open_job = None
        if self._close_job is None:
            self._close_job = self.after(self._CLOSE_DELAY, self._close_flyout)

    def _open_flyout(self):
        self._open_job = None
        app = self._flyout._app
        app.update_idletasks()
        rx = self.winfo_rootx() - app.winfo_rootx() + self.winfo_width() // 2
        ry = self.winfo_rooty() - app.winfo_rooty()
        self._flyout.show(rx, ry, self._name, self._tz,
                          close_delay=self._CLOSE_DELAY)

    def _close_flyout(self):
        self._close_job = None
        if self._flyout.winfo_ismapped():
            self._flyout.close()


# ── Scaled hover overlay for city strip tiles ────────────────────────────────

class CityColumnOverlay(tk.Frame):
    """
    Scaled-up ghost of a CityColumn that floats on the root window.
    Appears immediately on column hover; hides when mouse leaves both
    the overlay and its associated column.
    """
    _ICON_SIZE = round(CityColumn._ICON_SIZE * STRIP_HOVER_SCALE)
    _NAME_SIZE = round(CityColumn._NAME_SIZE * STRIP_HOVER_SCALE)
    _TIME_SIZE = round(CityColumn._TIME_SIZE * STRIP_HOVER_SCALE)

    def __init__(self, app: tk.Tk):
        super().__init__(app, bg=CARD_BG, padx=8, pady=5,
                         highlightthickness=1, highlightbackground=BORDER_CLR)
        self._app    = app
        self._column = None  # currently-hovered CityColumn

        self._icon_lbl = tk.Label(self, text="", bg=CARD_BG, fg=STRIP_DAY_FG,
                                  font=("Sans", self._ICON_SIZE))
        self._icon_lbl.pack()
        self._name_lbl = tk.Label(self, text="", bg=CARD_BG, fg=DATE_FG,
                                  font=("Sans", self._NAME_SIZE))
        self._name_lbl.pack()
        self._time_lbl = tk.Label(self, text="", bg=CARD_BG, fg=TIME_FG,
                                  font=("Monospace", self._TIME_SIZE, "bold"))
        self._time_lbl.pack()

        for w in (self, self._icon_lbl, self._name_lbl, self._time_lbl):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

    def show_over(self, column: CityColumn):
        self._column = column
        self._icon_lbl.config(text=column._icon_lbl.cget("text"),
                               fg=column._icon_lbl.cget("fg"))
        self._name_lbl.config(text=column._name)
        self._time_lbl.config(textvariable=column._t_var,
                               fg=column._time_lbl.cget("fg"))
        self._app.update_idletasks()
        ow = self.winfo_reqwidth()
        oh = self.winfo_reqheight()
        col_x = column.winfo_rootx() - self._app.winfo_rootx()
        col_y = column.winfo_rooty() - self._app.winfo_rooty()
        col_w = column.winfo_width()
        col_h = column.winfo_height()
        # Centre horizontally; anchor bottom of overlay to bottom of column
        # so it appears to grow upward
        ox = col_x + col_w // 2 - ow // 2
        oy = col_y + col_h - oh
        self.place(x=ox, y=oy, width=ow)
        self.lift()

    def hide(self):
        self._column = None
        self.place_forget()

    def _on_enter(self, _event):
        if self._column:
            self._column._flyout._cancel_close()

    def _on_leave(self, event):
        col = self._column
        under = self.winfo_containing(event.x_root, event.y_root)
        w = under
        while w is not None:
            if w is self or (col and (w is col or w is col._flyout)):
                return
            w = getattr(w, "master", None)
        self.hide()
        if col and col._close_job is None:
            col._close_job = col.after(col._CLOSE_DELAY, col._close_flyout)


# ── Bottom city-time strip ──────────────────────────────────────────────────

class CityStrip(tk.Frame):

    _BTN = dict(bg="#21262d", fg=CITY_FG, activebackground=BORDER_CLR,
                activeforeground=CITY_FG, relief=tk.FLAT, bd=0,
                font=("Sans", 14), padx=14, pady=0, cursor="hand2")

    def __init__(self, parent, app: tk.Tk, cities, flyout: CityFlyout,
                 overlay: CityColumnOverlay, visible: int = STRIP_VISIBLE):
        super().__init__(parent, bg=STRIP_BG)
        self._visible = visible
        self._offset  = 0
        self._flyout  = flyout
        self._overlay = overlay

        self._entries: list[tuple[str, ZoneInfo, tk.StringVar]] = []
        for name, tz_name, _lat, _lon in cities:
            self._entries.append((name, ZoneInfo(tz_name), tk.StringVar()))
        self._columns: list[CityColumn] = []

        self._btn_l = tk.Button(self, text="◀", command=self._scroll_left,
                                **self._BTN)
        self._btn_l.pack(side=tk.LEFT, fill=tk.Y)

        self._city_frame = tk.Frame(self, bg=STRIP_BG)
        self._city_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._btn_r = tk.Button(self, text="▶", command=self._scroll_right,
                                **self._BTN)
        self._btn_r.pack(side=tk.RIGHT, fill=tk.Y)

        self._render()

    def refresh(self):
        for _, tz, t_var in self._entries:
            t_var.set(datetime.datetime.now(tz).strftime("%H:%M"))
        for col in self._columns:
            now = datetime.datetime.now(col._tz)
            day = now.hour in STRIP_DAY_HOURS
            col.update_icon(STRIP_DAY_ICON     if day else STRIP_NIGHT_ICON,
                            STRIP_DAY_FG       if day else STRIP_NIGHT_FG,
                            STRIP_DAY_TIME_FG  if day else STRIP_NIGHT_TIME_FG)

    def _render(self):
        for w in self._city_frame.winfo_children():
            w.destroy()
        self._columns = []
        for name, tz, t_var in self._entries[self._offset:
                                             self._offset + self._visible]:
            now = datetime.datetime.now(tz)
            day = now.hour in STRIP_DAY_HOURS
            col = CityColumn(self._city_frame, name, t_var, tz, self._flyout, self._overlay,
                             STRIP_DAY_ICON    if day else STRIP_NIGHT_ICON,
                             STRIP_DAY_FG      if day else STRIP_NIGHT_FG,
                             STRIP_DAY_TIME_FG if day else STRIP_NIGHT_TIME_FG)
            col.pack(side=tk.LEFT, fill=tk.Y, expand=True)
            self._columns.append(col)
        max_off = max(0, len(self._entries) - self._visible)
        self._btn_l.config(state=tk.NORMAL if self._offset > 0      else tk.DISABLED)
        self._btn_r.config(state=tk.NORMAL if self._offset < max_off else tk.DISABLED)

    def _scroll_left(self):
        if self._offset > 0:
            self._flyout.close()
            self._overlay.hide()
            self._offset -= 1
            self._render()

    def _scroll_right(self):
        if self._offset < len(self._entries) - self._visible:
            self._flyout.close()
            self._overlay.hide()
            self._offset += 1
            self._render()


# ── Main window ─────────────────────────────────────────────────────────────

# ── Settings window ──────────────────────────────────────────────────────────

class SettingsWindow(tk.Toplevel):
    """Floating settings panel opened via the gear button in the header."""

    def __init__(self, app: tk.Tk, strip_var: tk.BooleanVar,
                 on_strip_toggle):
        super().__init__(app)
        self.title("Settings")
        self.configure(bg=CARD_BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

        tk.Label(self, text="Display", bg=CARD_BG, fg=DATE_FG,
                 font=("Sans", 9, "bold"), padx=12, pady=6,
                 anchor="w").pack(fill=tk.X)
        tk.Frame(self, bg=BORDER_CLR, height=1).pack(fill=tk.X, padx=12)
        tk.Checkbutton(self, text="Show city strip",
                       variable=strip_var, command=on_strip_toggle,
                       bg=CARD_BG, fg=CITY_FG,
                       selectcolor=BG, activebackground=CARD_BG,
                       activeforeground=CITY_FG,
                       font=("Sans", 10), padx=12, pady=8,
                       anchor="w", cursor="hand2").pack(fill=tk.X)
        self.withdraw()

    def toggle(self, near_x: int, near_y: int):
        if self.winfo_viewable():
            self.withdraw()
        else:
            self.geometry(f"+{near_x}+{near_y}")
            self.deiconify()
            self.lift()
            self.focus_force()


# ── Main window ──────────────────────────────────────────────────────────────

class WorldClockApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("World Clock")
        self.configure(bg=BG)
        self.minsize(800, 480)
        self._flyout       = CityFlyout(self)
        self._overlay      = CityColumnOverlay(self)
        self._strip_var    = tk.BooleanVar(value=False)
        self._build_header()
        tk.Frame(self, bg=SEP_CLR, height=1).pack(fill=tk.X)
        self._build_strip()
        self._build_map()
        self._settings_win = SettingsWindow(self, self._strip_var,
                                            self._apply_strip_visible)
        self._apply_strip_visible()
        self._tick()

    def _build_header(self):
        bar = tk.Frame(self, bg=HEADER_BG)
        bar.pack(fill=tk.X)
        tk.Label(bar, text="  WORLD CLOCK",
                 bg=HEADER_BG, fg=CITY_FG,
                 font=("Sans", 15, "bold"), pady=8).pack(side=tk.LEFT)
        self._gear_btn = tk.Button(bar, text="⚙",
                                   command=self._open_settings,
                                   bg=HEADER_BG, fg=DATE_FG,
                                   activebackground=CARD_BG, activeforeground=CITY_FG,
                                   relief=tk.FLAT, bd=0,
                                   font=("Sans", 13), padx=10, cursor="hand2")
        self._gear_btn.pack(side=tk.RIGHT)
        self._utc_var = tk.StringVar()
        tk.Label(bar, textvariable=self._utc_var,
                 bg=HEADER_BG, fg=DATE_FG,
                 font=("Monospace", 11), padx=16).pack(side=tk.RIGHT)

    def _build_map(self):
        self._map = GeochronCanvas(self, CITIES, self._flyout)
        self._map.pack(fill=tk.BOTH, expand=True)

    def _build_strip(self):
        self._strip_sep = tk.Frame(self, bg=SEP_CLR, height=1)
        self._strip = CityStrip(self, self, CITIES, self._flyout, self._overlay)
        self._strip.pack(fill=tk.X, side=tk.BOTTOM)
        self._strip_sep.pack(fill=tk.X, side=tk.BOTTOM)

    def _open_settings(self):
        self.update_idletasks()
        bx = self._gear_btn.winfo_rootx()
        by = self._gear_btn.winfo_rooty() + self._gear_btn.winfo_height()
        self._settings_win.toggle(bx, by)

    def _apply_strip_visible(self):
        if self._strip_var.get():
            self._strip.pack(fill=tk.X, side=tk.BOTTOM)
            self._strip_sep.pack(fill=tk.X, side=tk.BOTTOM)
        else:
            self._flyout.close()
            self._overlay.hide()
            self._strip.pack_forget()
            self._strip_sep.pack_forget()

    def _tick(self):
        utc = datetime.datetime.now(datetime.timezone.utc)
        self._utc_var.set(f"UTC  {utc.strftime('%H:%M:%S   %d %b %Y')}  ")
        self._map.refresh(utc_now=utc)
        if self._strip_var.get():
            self._strip.refresh()
        self._flyout.refresh_time()
        self.after(1000, self._tick)


if __name__ == "__main__":
    app = WorldClockApp()
    app.mainloop()
