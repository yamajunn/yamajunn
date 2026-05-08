from __future__ import annotations

import heapq
import html
import json
import math
import os
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
            height_cost = height[ny][nx] * 1.4
            slope_cost = abs(height[ny][nx] - height[cy][cx]) * 3.2
            score = g_score[current] + move_cost + height_cost + slope_cost
            if score < g_score.get((nx, ny), float("inf")):
                came_from[(nx, ny)] = current
                g_score[(nx, ny)] = score
                heapq.heappush(queue, (score + heuristic((nx, ny), goal), (nx, ny)))
    return []


def clamp(v: int) -> int:
    return max(0, min(255, v))


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def shade(color: str, factor: float) -> str:
    r, g, b = hex_to_rgb(color)
    return f"#{clamp(int(r * factor)):02x}{clamp(int(g * factor)):02x}{clamp(int(b * factor)):02x}"


def grass_color(v: float) -> str:
    palette = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
    return palette[min(4, max(0, int(round(v * 4))))]


def generate_svg(grid: list[list[int]]) -> str:
    heights = normalized_heights(grid)
    path = astar(heights)

    # View parameters.
    # Increase tile_h/depth_y and decrease z_scale to look more from above.
    # Decrease tile_h/depth_y and increase z_scale to look more from the side.
    tile_w = 18.0
    tile_h = 10.5
    depth_x = 6.8
    depth_y = 11.0
    z_scale = 28.0
    margin_x = 24.0
    margin_y = 14.0

    width = int(margin_x * 2 + COLS * tile_w + ROWS * depth_x + tile_w)
    height_px = int(margin_y * 2 + ROWS * depth_y + tile_h + z_scale + 14)
    base_y = margin_y + z_scale + 7.0

    def top_origin(x: int, y: int) -> tuple[float, float]:
        z = heights[y][x] * z_scale
        return margin_x + x * tile_w + y * depth_x, base_y + y * depth_y - z

    def top_center(x: int, y: int) -> tuple[float, float]:
        ox, oy = top_origin(x, y)
        return ox + tile_w / 2 + depth_x / 2, oy + tile_h / 2

    def points(poly: list[tuple[float, float]]) -> str:
        return " ".join(f"{x:.2f},{y:.2f}" for x, y in poly)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height_px}" viewBox="0 0 {width} {height_px}" role="img">',
        f'<rect width="{width}" height="{height_px}" rx="12" fill="#0d1117"/>',
    ]

    cells: list[tuple[float, int, int]] = []
    for y in range(ROWS):
        for x in range(COLS):
            ox, oy = top_origin(x, y)
            z = heights[y][x] * z_scale
            # Painter key: draw upper/far cells first, lower/near cells later.
            # Include x/y tie breakers so diagonal neighbors are stable.
            bottom_y = oy + depth_y + z
            key = bottom_y + x * 0.01 + y * 0.25
            cells.append((key, x, y))

    for _, x, y in sorted(cells):
        h = heights[y][x]
        z = h * z_scale
        ox, oy = top_origin(x, y)
        base = grass_color(h)

        top = [
            (ox, oy),
            (ox + tile_w, oy),
            (ox + tile_w + depth_x, oy + depth_y),
            (ox + depth_x, oy + depth_y),
        ]
        front = [
            (ox + depth_x, oy + depth_y),
            (ox + tile_w + depth_x, oy + depth_y),
            (ox + tile_w + depth_x, oy + depth_y + z),
            (ox + depth_x, oy + depth_y + z),
        ]
        right = [
            (ox + tile_w, oy),
            (ox + tile_w + depth_x, oy + depth_y),
            (ox + tile_w + depth_x, oy + depth_y + z),
            (ox + tile_w, oy + z),
        ]

        if z > 0.2:
            parts.append(f'<polygon points="{points(right)}" fill="{shade(base, 0.50)}"/>')
            parts.append(f'<polygon points="{points(front)}" fill="{shade(base, 0.65)}"/>')
        parts.append(f'<polygon points="{points(top)}" fill="{base}" stroke="#0d1117" stroke-width="0.7"/>')

    if path:
        line_points = " ".join(f"{top_center(x, y)[0]:.2f},{top_center(x, y)[1]:.2f}" for x, y in path)
        parts.append(f'<polyline points="{line_points}" fill="none" stroke="#f2cc60" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>')
        sx, sy = top_center(*path[0])
        gx, gy = top_center(*path[-1])
        parts.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="3.8" fill="#f2cc60" stroke="#0d1117" stroke-width="1"/>')
        parts.append(f'<circle cx="{gx:.2f}" cy="{gy:.2f}" r="3.8" fill="#f2cc60" stroke="#0d1117" stroke-width="1"/>')

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    grid = build_grid(fetch_counts())
    OUT.write_text(generate_svg(grid), encoding="utf-8")


if __name__ == "__main__":
    main()
