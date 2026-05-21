from __future__ import annotations

import hashlib
import heapq
import html
import json
import math
import os
import random
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

USERNAME = "yamajunn"
OUT = Path("assets/grass_terrain.svg")
ROWS = 7
COLS = 53
JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class DayCount:
    day: date
    count: int


def fetch_graphql_counts(token: str) -> list[DayCount]:
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    body = json.dumps({"query": query, "variables": {"login": USERNAME}}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{USERNAME}-grass-terrain",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.loads(res.read().decode("utf-8"))
    if data.get("errors"):
        raise RuntimeError(data["errors"])

    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    return [
        DayCount(date.fromisoformat(day["date"]), int(day["contributionCount"]))
        for week in weeks
        for day in week["contributionDays"]
    ]


def fetch_public_html_counts() -> list[DayCount]:
    today = datetime.now(JST).date()
    url = f"https://github.com/users/{USERNAME}/contributions?to={today.isoformat()}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "text/html", "User-Agent": f"{USERNAME}-grass-terrain"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        text = res.read().decode("utf-8", errors="replace")

    counts: list[DayCount] = []
    pattern = r"<[^>]+data-date=\"(\d{4}-\d{2}-\d{2})\"[^>]*>"
    for m in re.finditer(pattern, text):
        tag = m.group(0)
        attrs = dict(re.findall(r"([a-zA-Z0-9_:-]+)=\"([^\"]*)\"", tag))
        day = date.fromisoformat(attrs["data-date"])
        if "data-count" in attrs:
            count = int(attrs["data-count"])
        else:
            label = html.unescape(attrs.get("aria-label", ""))
            if label.lower().startswith("no contributions"):
                count = 0
            else:
                found = re.search(r"(\d+)\s+contribution", label)
                count = int(found.group(1)) if found else int(attrs.get("data-level", "0"))
        counts.append(DayCount(day, count))

    if not counts:
        raise RuntimeError("Could not parse contribution calendar")
    return counts


def fetch_counts() -> list[DayCount]:
    token = os.getenv("PROFILE_TOKEN")
    return fetch_graphql_counts(token) if token else fetch_public_html_counts()


def sunday_of(day: date) -> date:
    return day - timedelta(days=(day.weekday() + 1) % 7)


def build_grid(days: list[DayCount]) -> list[list[int]]:
    by_day = {item.day: item.count for item in days}
    last_day = max(by_day)
    start_day = sunday_of(last_day) - timedelta(days=7 * (COLS - 1))

    grid = [[0 for _ in range(COLS)] for _ in range(ROWS)]
    for x in range(COLS):
        for y in range(ROWS):
            grid[y][x] = by_day.get(start_day + timedelta(days=x * 7 + y), 0)
    return grid


def normalized_heights(grid: list[list[int]]) -> list[list[float]]:
    max_log = max([math.log1p(v) for row in grid for v in row] + [1.0])
    return [[math.log1p(v) / max_log for v in row] for row in grid]


def astar(height: list[list[float]]) -> list[tuple[int, int]]:
    start = (0, ROWS // 2)
    goal = (COLS - 1, ROWS // 2)

    def heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def neighbors(x: int, y: int):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < COLS and 0 <= ny < ROWS:
                yield nx, ny

    queue: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score = {start: 0.0}

    while queue:
        _, current = heapq.heappop(queue)
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return list(reversed(path))

        cx, cy = current
        for nx, ny in neighbors(cx, cy):
            move_cost = 1.0
            height_cost = height[ny][nx] * 1.2
            slope_cost = abs(height[ny][nx] - height[cy][cx]) * 2.8
            score = g_score[current] + move_cost + height_cost + slope_cost
            if score < g_score.get((nx, ny), float("inf")):
                came_from[(nx, ny)] = current
                g_score[(nx, ny)] = score
                heapq.heappush(queue, (score + heuristic((nx, ny), goal), (nx, ny)))
    return []


def clamp(value: int) -> int:
    return max(0, min(255, value))


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def shade(color: str, factor: float) -> str:
    r, g, b = hex_to_rgb(color)
    return f"#{clamp(int(r * factor)):02x}{clamp(int(g * factor)):02x}{clamp(int(b * factor)):02x}"


def grass_color(value: float) -> str:
    palette = ["#0b1020", "#124a3a", "#1d7c59", "#34b37a", "#8af5b2"]
    return palette[min(4, max(0, int(round(value * 4))))]


def daily_noise_seed(grid: list[list[int]], stamp: date) -> int:
    payload = f"{stamp.isoformat()}|" + ",".join(str(v) for row in grid for v in row)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def generate_svg(grid: list[list[int]], stamp: date) -> str:
    heights = normalized_heights(grid)
    path = astar(heights)
    rng = random.Random(daily_noise_seed(grid, stamp))

    # Projection is intentionally simple and stable:
    # - columns are separated horizontally, so neighboring columns do not fight for z-order
    # - rows are drawn from back to front
    # - each column is drawn as right face -> front face -> top face
    tile_w = 14.0
    tile_h = 8.0
    x_step = 19.0
    depth_x = 7.0
    depth_y = 10.0
    z_scale = 34.0
    margin_x = 22.0
    margin_y = 14.0

    width = int(margin_x * 2 + (COLS - 1) * x_step + tile_w + ROWS * depth_x + 8)
    height_px = int(margin_y * 2 + ROWS * depth_y + tile_h + z_scale + 16)
    ground_y = margin_y + z_scale + 8.0

    def top_origin(x: int, y: int) -> tuple[float, float]:
        z = heights[y][x] * z_scale
        ox = margin_x + x * x_step + y * depth_x
        oy = ground_y + y * depth_y - z
        return ox, oy

    def top_center(x: int, y: int) -> tuple[float, float]:
        ox, oy = top_origin(x, y)
        return ox + tile_w / 2 + depth_x / 2, oy + tile_h / 2

    def pts(poly: list[tuple[float, float]]) -> str:
        return " ".join(f"{px:.2f},{py:.2f}" for px, py in poly)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height_px}" viewBox="0 0 {width} {height_px}" role="img">',
        "<defs>",
        '<linearGradient id="bgGrad" x1="0" y1="0" x2="0" y2="1">',
        '<stop offset="0%" stop-color="#04070f"/>',
        '<stop offset="100%" stop-color="#071225"/>',
        "</linearGradient>",
        '<linearGradient id="fogGrad" x1="0" y1="0" x2="1" y2="1">',
        '<stop offset="0%" stop-color="#7dd3fc" stop-opacity="0.12"/>',
        '<stop offset="70%" stop-color="#34d399" stop-opacity="0.00"/>',
        "</linearGradient>",
        '<radialGradient id="sunGlow" cx="78%" cy="16%" r="32%">',
        '<stop offset="0%" stop-color="#67e8f9" stop-opacity="0.24"/>',
        '<stop offset="100%" stop-color="#67e8f9" stop-opacity="0.00"/>',
        "</radialGradient>",
        '<filter id="neonBlur" x="-50%" y="-50%" width="200%" height="200%">',
        '<feGaussianBlur stdDeviation="3"/>',
        "</filter>",
        "</defs>",
        f'<rect width="{width}" height="{height_px}" rx="12" fill="url(#bgGrad)"/>',
        f'<rect width="{width}" height="{height_px}" rx="12" fill="url(#fogGrad)"/>',
        f'<circle cx="{width - 120}" cy="26" r="84" fill="url(#sunGlow)"/>',
    ]

    for _ in range(22):
        sx = rng.uniform(20, width - 20)
        sy = rng.uniform(10, height_px * 0.45)
        r = rng.uniform(0.5, 1.8)
        op = rng.uniform(0.08, 0.32)
        parts.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="{r:.2f}" fill="#e2e8f0" opacity="{op:.2f}"/>')


    for y in range(ROWS):
        for x in range(COLS):
            h = heights[y][x]
            z = h * z_scale
            ox, oy = top_origin(x, y)
            base = grass_color(h)
            tint = shade("#67e8f9", 0.50 + h * 0.55)

            top = [
                (ox, oy),
                (ox + tile_w, oy),
                (ox + tile_w + depth_x, oy + depth_y),
                (ox + depth_x, oy + depth_y),
            ]
            right = [
                (ox + tile_w, oy),
                (ox + tile_w + depth_x, oy + depth_y),
                (ox + tile_w + depth_x, oy + depth_y + z),
                (ox + tile_w, oy + z),
            ]
            front = [
                (ox + depth_x, oy + depth_y),
                (ox + tile_w + depth_x, oy + depth_y),
                (ox + tile_w + depth_x, oy + depth_y + z),
                (ox + depth_x, oy + depth_y + z),
            ]

            shadow_opacity = min(0.26, 0.06 + h * 0.24)
            shadow = [
                (ox + depth_x + 1.2, oy + depth_y + z + 1.0),
                (ox + tile_w + depth_x + 1.2, oy + depth_y + z + 1.0),
                (ox + tile_w + depth_x + 5.2, oy + depth_y + z + 5.2),
                (ox + depth_x + 5.2, oy + depth_y + z + 5.2),
            ]
            if z > 0.15:
                parts.append(
                    f'<polygon points="{pts(shadow)}" fill="#020617" opacity="{shadow_opacity:.2f}"/>'
                )
                parts.append(f'<polygon points="{pts(right)}" fill="{shade(base, 0.42)}"/>')
                parts.append(f'<polygon points="{pts(front)}" fill="{shade(base, 0.56)}"/>')
            parts.append(f'<polygon points="{pts(top)}" fill="{base}" stroke="#022c22" stroke-width="0.52"/>')
            if z > 0.15:
                highlight = [
                    (ox + 1.0, oy + 0.8),
                    (ox + tile_w - 1.0, oy + 0.8),
                    (ox + tile_w + depth_x - 1.8, oy + depth_y - 0.8),
                    (ox + depth_x + 1.2, oy + depth_y - 0.8),
                ]
                parts.append(
                    f'<polygon points="{pts(highlight)}" fill="#ecfeff" opacity="{0.05 + h * 0.14:.2f}"/>'
                )
                ridge = [
                    (ox + tile_w * 0.58, oy + 0.6),
                    (ox + tile_w + depth_x - 1.2, oy + depth_y * 0.62),
                    (ox + tile_w + depth_x - 1.2, oy + depth_y * 0.78),
                    (ox + tile_w * 0.58, oy + 0.76),
                ]
                parts.append(f'<polygon points="{pts(ridge)}" fill="{tint}" opacity="{0.15 + h * 0.30:.2f}"/>')
                if h > 0.62 and rng.random() < 0.22:
                    cx, cy = top_center(x, y)
                    parts.append(f'<circle cx="{cx:.2f}" cy="{cy - 1.2:.2f}" r="{1.2 + h:.2f}" fill="{tint}" opacity="0.45" filter="url(#neonBlur)"/>')

    if path:
        line = " ".join(f"{top_center(x, y)[0]:.2f},{top_center(x, y)[1]:.2f}" for x, y in path)
        parts.append(f'<polyline points="{line}" fill="none" stroke="#67e8f9" stroke-opacity="0.35" stroke-width="7" stroke-linecap="round" stroke-linejoin="round" filter="url(#neonBlur)"/>')
        parts.append(f'<polyline points="{line}" fill="none" stroke="#a7f3d0" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>')
        sx, sy = top_center(*path[0])
        gx, gy = top_center(*path[-1])
        parts.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="3.9" fill="#a7f3d0" stroke="#083344" stroke-width="1"/>')
        parts.append(f'<circle cx="{gx:.2f}" cy="{gy:.2f}" r="3.9" fill="#a7f3d0" stroke="#083344" stroke-width="1"/>')

    parts.append(
        f'<text x="{width - 16}" y="{height_px - 14}" text-anchor="end" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="10" fill="#94a3b8" opacity="0.85">{stamp.isoformat()}</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(JST).date()
    grid = build_grid(fetch_counts())
    OUT.write_text(generate_svg(grid, stamp), encoding="utf-8")


if __name__ == "__main__":
    main()
