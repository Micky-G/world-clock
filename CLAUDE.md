# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make build   # build the Docker image (downloads map assets from NASA at build time)
make run     # build + launch the GUI app (calls xhost +local:docker automatically)
make shell   # build + open an interactive shell inside the container
```

All development happens inside the Docker container — nothing is installed on the host except Docker itself. The source directory is bind-mounted read-only at `/workspace` when running, read-write when using `make shell`.

## Architecture

Single-file app: `world_clock.py`. All logic, layout, and rendering lives there.

**Data flow each second** (`WorldClockApp._tick` → every 1 000 ms):
1. Update UTC header label
2. `GeochronCanvas.refresh()` — redraws city dots/labels every second; recomposites the full map only when the UTC minute changes (~100–150 ms Pillow operation)
3. `CityStrip.refresh()` — updates city-time `StringVar` labels and calls `update_icon()` on each visible `CityColumn`
4. `CityFlyout.refresh_time()` — updates the time label in any open flyout

**Map rendering** (once per minute inside `GeochronCanvas._redraw_map`):
- `_solar_decl_eot()` → solar declination + equation of time (Spencer 1971, pure stdlib)
- `_subsolar_lon()` → longitude where sun is directly overhead
- `_blend_mask()` → Pillow `L`-mode grayscale image (255 = day, 0 = night) computed column-by-column. Each column uses `_elevation_lat()` to find the terminator (elev = 0°) and civil-twilight boundary (elev = −6°), drawing a soft gradient between them.
- `Image.composite(day_img, night_img, mask)` → blends the two NASA source maps
- Result is scaled to the current canvas size and pushed to a `tk.Canvas` via `ImageTk.PhotoImage`. The `PhotoImage` **must** be kept as `self._photo` (instance attribute) — if it goes out of scope tkinter garbage-collects it and the image disappears.

**Map projection**: both NASA source images are equirectangular (plate carrée / EPSG:4326). Coordinate conversion: `x = (lon + 180) / 360 * W`, `y = (90 − lat) / 180 * H`.

**Map assets** (baked into the Docker image at build time, not in the repo):
- `/opt/worldclock/assets/world_map_day.png` — NASA Blue Marble 2048×1024
- `/opt/worldclock/assets/world_map_night.png` — NASA Earth at Night, resized to match day map

**City info flyout** (`CityFlyout`):
- Single shared instance created on the root `tk.Tk` window and passed to both `GeochronCanvas` and `CityStrip`
- Triggered by clicking a city dot on the map, or hovering a strip tile for 500 ms
- Fetches Wikipedia summary + thumbnail in a background thread (`_fetch_wiki`, `_download_image`) using the Wikipedia REST API
- Positioned using root-relative coordinates (`winfo_rootx/y()` relative to `app.winfo_rootx/y()`) so it can appear anywhere on screen
- Closes after 1 000 ms (map click) or 500 ms (strip hover) once mouse leaves; close timer is cancelled when mouse re-enters the flyout
- `_app` attribute stores the root window reference — **do not name this `_root`** as that shadows a tkinter internal method and causes `TypeError`

**Strip hover overlay** (`CityColumnOverlay`):
- Second shared instance on the root window, passed through `CityStrip` → `CityColumn`
- Shows a scaled-up version of the hovered tile (`STRIP_HOVER_SCALE = 1.25`) immediately (no delay), anchored so its bottom aligns with the column bottom — gives the appearance of the button growing upward
- Uses `winfo_reqheight()` to measure the overlay height before placement — **never** temporarily place at `y=0` to measure, it causes a visible flash
- Hides when mouse leaves both the column and the overlay itself; enter/leave bindings on the overlay cancel the flyout's close timer to keep the flyout open while mouse is in the enlarged area

**Bottom strip** (`CityStrip`):
- Shows `STRIP_VISIBLE = 10` city tiles at a time with `◀`/`▶` scroll buttons
- `CityColumn` tiles have 500 ms open / 500 ms close delays for the flyout; the overlay appears and hides immediately
- Each tile shows a day/night icon (top), city name, and local time. Icon character, icon colour, and time label colour are all driven by the city's local hour — day constants: `STRIP_DAY_ICON`, `STRIP_DAY_FG`, `STRIP_DAY_TIME_FG`; night constants: `STRIP_NIGHT_ICON`, `STRIP_NIGHT_FG`, `STRIP_NIGHT_TIME_FG`; boundary: `STRIP_DAY_HOURS`. All are configurable at the top of the file.
- `CityStrip` keeps `self._columns` (the currently rendered `CityColumn` instances). `refresh()` calls `col.update_icon(icon, icon_fg, time_fg)` on each; `_render()` rebuilds `self._columns` on every scroll. Non-visible columns are destroyed and recreated, so their icon and colours are computed fresh in `_render()` rather than maintained via a `StringVar` (tkinter labels don't support a `StringVar` for `fg`)

## Development iteration

`make shell` bind-mounts the source read-write. Inside the container, run `python3 /workspace/world_clock.py` directly and edit files on the host; restart the process to pick up changes. No test suite exists.

## Adding/removing cities

Edit the `CITIES` list in `world_clock.py`:
```python
("City Name", "Continent/Timezone", lat_float, lon_float),
```
Timezone strings must be valid IANA tz names (used directly by `zoneinfo.ZoneInfo`).

If the city's Wikipedia article title differs from its display name, add an entry to `_WIKI_TITLES` (e.g. `"New York": "New_York_City"`).

## Layout building order

`WorldClockApp.__init__` calls `_build_strip` **before** `_build_map`. This is required: tkinter resolves `side=tk.BOTTOM` relative to unallocated space at the time of packing, so the strip must be packed first or it will be hidden behind the canvas.

## Key constraints

- **No pip / no third-party Python packages** — only stdlib + `Pillow` (installed via `python3-pil` apt package). Do not add pip dependencies.
- **Multiline Python in `RUN` Dockerfile instructions** will cause a parse error if any line starts with `from` — Docker misreads it as a `FROM` instruction. Keep `-c` arguments as one-liners with semicolons.
- **Ubuntu 24.04** ships a built-in `ubuntu` user at UID/GID 1000; the Dockerfile renames it to `vscode` rather than creating a new user, to avoid a GID collision.
- The `<Configure>` binding on `GeochronCanvas` handles window resize by forcing a full map redraw.
- `winfo_containing(x_root, y_root)` + walking the `master` chain is the pattern used throughout for "is the mouse still inside widget X or its children" checks in `_on_leave` handlers.
