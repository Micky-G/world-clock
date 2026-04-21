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
1. Update UTC header label (every second)
2. `GeochronCanvas.refresh()` — redraws city dots/labels every second; recomposites the full map only when the UTC minute changes (~100–150 ms Pillow operation)
3. `CityStrip.refresh()` — updates 24 `StringVar` city-time labels (every second)

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

## Adding/removing cities

Edit the `CITIES` list in `world_clock.py`:
```python
("City Name", "Continent/Timezone", lat_float, lon_float),
```
Timezone strings must be valid IANA tz names (used directly by `zoneinfo.ZoneInfo`).

## Key constraints

- **No pip / no third-party Python packages** — only stdlib + `Pillow` (installed via `python3-pil` apt package). Do not add pip dependencies.
- **Multiline Python in `RUN` Dockerfile instructions** will cause a parse error if any line starts with `from` — Docker misreads it as a `FROM` instruction. Keep `-c` arguments as one-liners with semicolons.
- **Ubuntu 24.04** ships a built-in `ubuntu` user at UID/GID 1000; the Dockerfile renames it to `vscode` rather than creating a new user, to avoid a GID collision.
- The `<Configure>` binding on `GeochronCanvas` handles window resize by forcing a full map redraw.
