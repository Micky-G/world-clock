#!/usr/bin/env python3
"""World Clock — Geochron-style map with day/night blend and city-lights at night."""

import tkinter as tk
import datetime
import math
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
SEP_CLR    = "#21262d"
CITY_FG    = "#e6edf3"
TIME_FG    = "#58a6ff"
DATE_FG    = "#8b949e"

# ── Cities: (name, tz, lat°N, lon°E) ───────────────────────────────────────
CITIES = [
    ("New York",     "America/New_York",                 40.71,  -74.01),
    ("Los Angeles",  "America/Los_Angeles",              34.05, -118.24),
    ("Chicago",      "America/Chicago",                  41.88,  -87.63),
    ("Toronto",      "America/Toronto",                  43.65,  -79.38),
    ("São Paulo",    "America/Sao_Paulo",               -23.55,  -46.63),
    ("Buenos Aires", "America/Argentina/Buenos_Aires",  -34.60,  -58.38),
    ("London",       "Europe/London",                    51.51,   -0.13),
    ("Paris",        "Europe/Paris",                     48.85,    2.35),
    ("Berlin",       "Europe/Berlin",                    52.52,   13.41),
    ("Amsterdam",    "Europe/Amsterdam",                 52.37,    4.90),
    ("Moscow",       "Europe/Moscow",                    55.75,   37.62),
    ("Cairo",        "Africa/Cairo",                     30.04,   31.24),
    ("Lagos",        "Africa/Lagos",                      6.45,    3.39),
    ("Nairobi",      "Africa/Nairobi",                   -1.29,   36.82),
    ("Dubai",        "Asia/Dubai",                       25.20,   55.27),
    ("Mumbai",       "Asia/Kolkata",                     19.08,   72.88),
    ("Bangkok",      "Asia/Bangkok",                     13.75,  100.52),
    ("Singapore",    "Asia/Singapore",                    1.35,  103.82),
    ("Hong Kong",    "Asia/Hong_Kong",                   22.32,  114.17),
    ("Shanghai",     "Asia/Shanghai",                    31.23,  121.47),
    ("Tokyo",        "Asia/Tokyo",                       35.68,  139.69),
    ("Seoul",        "Asia/Seoul",                       37.57,  126.98),
    ("Sydney",       "Australia/Sydney",                -33.87,  151.21),
    ("Auckland",     "Pacific/Auckland",                -36.86,  174.77),
]


# ── Solar position (Spencer 1971, pure stdlib) ──────────────────────────────

def _solar_decl_eot(dt_utc: datetime.datetime):
    """Return (declination_rad, equation_of_time_minutes)."""
    B    = math.radians((360 / 365) * (dt_utc.timetuple().tm_yday - 81))
    decl = math.radians(23.45) * math.sin(B)
    eot  = 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
    return decl, eot


def _subsolar_lon(dt_utc: datetime.datetime, eot_min: float) -> float:
    """Longitude (°) where the sun is directly overhead."""
    ut  = dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600
    lon = -(ut - 12) * 15 - (eot_min / 60) * 15
    return (lon + 180) % 360 - 180


def _elevation_lat(lon_deg: float, sub_lon_deg: float,
                   decl_rad: float, elev_deg: float = 0.0) -> float:
    """
    Latitude (°) at which solar elevation equals elev_deg for a given longitude.
    Uses the identity  a·sin(φ) + b·cos(φ) = R·sin(φ + ψ)  to solve analytically.
    Returns ±90 when no crossing exists (column entirely above/below that elevation).
    """
    H   = math.radians(lon_deg - sub_lon_deg)
    a   = math.sin(decl_rad)
    b   = math.cos(decl_rad) * math.cos(H)
    c   = math.sin(math.radians(elev_deg))
    R   = math.hypot(a, b)
    if R < 1e-9:
        return 90.0 if c >= 0 else -90.0
    ratio = c / R
    if ratio > 1.0:
        return 90.0
    if ratio < -1.0:
        return -90.0
    psi = math.atan2(b, a)
    return math.degrees(math.asin(ratio) - psi)


# ── Day/night blend mask ────────────────────────────────────────────────────

def _blend_mask(map_w: int, map_h: int,
                decl_rad: float, sub_lon: float) -> Image.Image:
    """
    Grayscale L-mode mask: 255 = full day, 0 = full night.
    A soft twilight gradient spans the civil twilight zone (solar elev 0° → −6°).

    When decl > 0 the sun is in the northern hemisphere:
      • high pixel-y (south) = night   →  day zone is at top, night at bottom
    When decl < 0 the sun is in the southern hemisphere:
      • low  pixel-y (north) = night   →  night zone is at top, day at bottom
    """
    mask  = Image.new("L", (map_w, map_h), 255)   # start fully lit
    draw  = ImageDraw.Draw(mask)
    north = decl_rad > 0          # True: night side is toward large pixel-y (south)

    for px in range(map_w):
        lon   = (px / map_w) * 360 - 180

        # Terminator (elev = 0°) and civil-twilight boundary (elev = −6°)
        t0_lat = _elevation_lat(lon, sub_lon, decl_rad, 0.0)
        t6_lat = _elevation_lat(lon, sub_lon, decl_rad, -6.0)

        # Convert latitudes to pixel-y  (90° → py=0, −90° → py=map_h)
        def to_py(lat):
            return max(0, min(map_h - 1, int((90 - lat) / 180 * map_h)))

        t0_py = to_py(t0_lat)
        t6_py = to_py(t6_lat)

        if north:
            # Night south of terminator  →  large py values
            # ordering: ... t0_py ... t6_py ... map_h
            #   day:      0 .. t0_py        mask=255 (default)
            #   twilight: t0_py .. t6_py    gradient 255 → 0
            #   night:    t6_py .. map_h    mask=0
            if t6_py < map_h:
                draw.rectangle([px, t6_py, px, map_h - 1], fill=0)
            band = max(1, t6_py - t0_py)
            for dy in range(band):
                py = t0_py + dy
                if 0 <= py < map_h:
                    mask.putpixel((px, py), int(255 * (1 - dy / band)))
        else:
            # Night north of terminator  →  small py values
            # ordering: 0 ... t6_py ... t0_py ...
            #   night:    0 .. t6_py        mask=0
            #   twilight: t6_py .. t0_py    gradient 0 → 255
            #   day:      t0_py .. map_h    mask=255 (default)
            if t6_py > 0:
                draw.rectangle([px, 0, px, t6_py], fill=0)
            band = max(1, t0_py - t6_py)
            for dy in range(band):
                py = t6_py + dy
                if 0 <= py < map_h:
                    mask.putpixel((px, py), int(255 * dy / band))

    return mask


# ── Geochron canvas ─────────────────────────────────────────────────────────

class GeochronCanvas(tk.Canvas):

    def __init__(self, parent, cities):
        super().__init__(parent, bg=BG, highlightthickness=0)
        self._cities   = [(n, ZoneInfo(tz), lat, lon)
                          for n, tz, lat, lon in cities]
        self._day_img  = Image.open(DAY_MAP_PATH).convert("RGB")
        self._night_img = Image.open(NIGHT_MAP_PATH).convert("RGB")
        # Ensure both images are the same size
        if self._night_img.size != self._day_img.size:
            self._night_img = self._night_img.resize(
                self._day_img.size, Image.LANCZOS)
        self._map_w, self._map_h = self._day_img.size
        self._photo    = None
        self._last_min = -1
        self._dw = self._dh = 1
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        self._dw, self._dh = event.width, event.height
        self.refresh(force=True)

    # ── Public ──────────────────────────────────────────────────────────────

    def refresh(self, utc_now: datetime.datetime = None, force: bool = False):
        if utc_now is None:
            utc_now = datetime.datetime.now(datetime.timezone.utc)
        if self._dw < 2 or self._dh < 2:
            return
        if force or utc_now.minute != self._last_min:
            self._redraw_map(utc_now)
            self._last_min = utc_now.minute
        else:
            self._draw_cities(utc_now)

    # ── Private ─────────────────────────────────────────────────────────────

    def _redraw_map(self, utc_now: datetime.datetime):
        decl, eot = _solar_decl_eot(utc_now)
        sub_lon   = _subsolar_lon(utc_now, eot)

        # Build grayscale blend mask then composite day/night images
        mask      = _blend_mask(self._map_w, self._map_h, decl, sub_lon)
        # composite(img1, img2, mask): mask=255 → img1, mask=0 → img2
        composite = Image.composite(self._day_img, self._night_img, mask)

        scaled     = composite.resize((self._dw, self._dh), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(scaled)
        self.delete("map")
        self.create_image(0, 0, anchor="nw", image=self._photo, tags="map")
        self.tag_lower("map")

        self._draw_cities(utc_now)
        self._draw_subsolar(sub_lon, decl)

    def _latlon_to_xy(self, lat: float, lon: float):
        x = (lon + 180) / 360 * self._dw
        y = (90 - lat)  / 180 * self._dh
        return int(x), int(y)

    def _draw_cities(self, utc_now: datetime.datetime):
        self.delete("cities")
        for name, tz, lat, lon in self._cities:
            x, y     = self._latlon_to_xy(lat, lon)
            now      = datetime.datetime.now(tz)
            time_str = now.strftime("%H:%M")
            daytime  = 6 <= now.hour < 20
            dot_fill = "#ffcc00" if daytime else "#4a9fd4"

            r = 3
            self.create_oval(x - r, y - r, x + r, y + r,
                             fill=dot_fill, outline="#ffffff",
                             width=1, tags="cities")
            self.create_text(x, y - 6, text=name,
                             fill="#ffffff", font=("Sans", 7, "bold"),
                             anchor="s", tags="cities")
            self.create_text(x, y + 6, text=time_str,
                             fill=dot_fill, font=("Monospace", 7),
                             anchor="n", tags="cities")

    def _draw_subsolar(self, sub_lon: float, decl_rad: float):
        self.delete("sun")
        sub_lat = math.degrees(decl_rad)
        x, y = self._latlon_to_xy(sub_lat, sub_lon)
        r = 6
        self.create_oval(x - r, y - r, x + r, y + r,
                         fill="#ffdd00", outline="#ffffff",
                         width=2, tags="sun")
        self.create_line(x - 12, y, x + 12, y,
                         fill="#ffdd00", width=1, tags="sun")
        self.create_line(x, y - 12, x, y + 12,
                         fill="#ffdd00", width=1, tags="sun")


# ── Bottom city-time strip ──────────────────────────────────────────────────

class CityStrip(tk.Frame):

    def __init__(self, parent, cities):
        super().__init__(parent, bg=STRIP_BG)
        self._entries = []

        for name, tz_name, _lat, _lon in cities:
            tz    = ZoneInfo(tz_name)
            t_var = tk.StringVar()

            col = tk.Frame(self, bg=CARD_BG, padx=6, pady=3)
            col.pack(side=tk.LEFT, fill=tk.Y, expand=True)

            tk.Label(col, text=name, bg=CARD_BG, fg=DATE_FG,
                     font=("Sans", 7)).pack()
            tk.Label(col, textvariable=t_var, bg=CARD_BG, fg=TIME_FG,
                     font=("Monospace", 9, "bold")).pack()

            self._entries.append((tz, t_var))

    def refresh(self):
        for tz, t_var in self._entries:
            t_var.set(datetime.datetime.now(tz).strftime("%H:%M"))


# ── Main window ─────────────────────────────────────────────────────────────

class WorldClockApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Geochron World Clock")
        self.configure(bg=BG)
        self.minsize(800, 480)
        self._build_header()
        tk.Frame(self, bg=SEP_CLR, height=1).pack(fill=tk.X)
        self._build_strip()
        self._build_map()
        self._tick()

    def _build_header(self):
        bar = tk.Frame(self, bg=HEADER_BG)
        bar.pack(fill=tk.X)
        tk.Label(bar, text="  GEOCHRON WORLD CLOCK",
                 bg=HEADER_BG, fg=CITY_FG,
                 font=("Sans", 15, "bold"), pady=8).pack(side=tk.LEFT)
        self._utc_var = tk.StringVar()
        tk.Label(bar, textvariable=self._utc_var,
                 bg=HEADER_BG, fg=DATE_FG,
                 font=("Monospace", 11), padx=16).pack(side=tk.RIGHT)

    def _build_map(self):
        self._map = GeochronCanvas(self, CITIES)
        self._map.pack(fill=tk.BOTH, expand=True)

    def _build_strip(self):
        tk.Frame(self, bg=SEP_CLR, height=1).pack(fill=tk.X)
        self._strip = CityStrip(self, CITIES)
        self._strip.pack(fill=tk.X, side=tk.BOTTOM)

    def _tick(self):
        utc = datetime.datetime.now(datetime.timezone.utc)
        self._utc_var.set(f"UTC  {utc.strftime('%H:%M:%S   %d %b %Y')}  ")
        self._map.refresh(utc_now=utc)
        self._strip.refresh()
        self.after(1000, self._tick)


if __name__ == "__main__":
    app = WorldClockApp()
    app.mainloop()
