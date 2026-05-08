from __future__ import annotations

import html
import json
import math
import os
import re
import heapq
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

USERNAME = "yamajunn"
OUT = Path("assets/grass_terrain.svg")
ROWS = 7
MAX_COLS = 53
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
    out: list[DayCount] = []
    for week in weeks:
        for d in week["contributionDays"]:
            out.append(DayCount(date.fromisoformat(d["date"]), int(d["contributionCount"])))
    return out


def fetch_public_html_counts() -> list[DayCount]:
    today = datetime.now(JST).date()
    url = f"https://github.com/users/{USERNAME}/contributions?to={today.isoformat()}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": f"{USERNAME}-grass-terrain",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        text = res.read().decode("utf-8", errors="replace")

    out: list[DayCount] = []
    for m in re.finditer(r"<[^>]+data-date=\"(\d{4}-\d{2}-\d{2})\"[^>]*>", text):
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
                n = re.search(r"(\d+)\s+contribution", label)
                count = int(n.group(1)) if n else int(attrs.get("data-level", "0"))

        out.append(DayCount(day, count))

    if not out:
        raise RuntimeError("Could not parse contribution calendar")
    return out


def fetch_counts() -> list[DayCount]:
    token = os.getenv("PROFILE_TOKEN")
    if token:
        return fetch_graphql_counts(token)
    return fetch_public_html_counts()


def sunday_of(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)


def build_grid(days: list[DayCount]) -> list[list[int]]:
    by_day = {d.day: d.count for d in days}
    max_day = max(by_day)
    start = sunday_of(max_day) - timedelta(days=7 * (MAX_COLS - 1))

    grid = [[0 for _ in range(MAX_COLS)] for _ in range(ROWS)]
    for col in range(MAX_COLS):
        for row in range(ROWS):
            day = start + timedelta(days=col * 7 + row)
            grid[row][col] = by_day.get(day, 0)
    return grid


def normalized_heights(grid: list[list[int]]) -> list[list[float]]:
    values = [v for row in grid for v in row]
    max_log = max([math.log1p(v) for v in values] + [1.0])
    return [[math.log1p(v) / max_log for v in row] for row in grid]


def astar(height: list[list[float]]) -> list[tuple[int, int]]:
    cols = len(height[0])
    start = (0, ROWS // 2)
    goal = (cols - 1, ROWS // 2)

    def h(a: tuple[int, int], b: tuple[int, int]) -> float:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def neighbors(x: int, y: int):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < cols and 0 <= ny < ROWS:
                yield nx, ny

    open_set: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score = {start: 0.0}

    while open_set:
        _, current = heapq.heappop(open_set)
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
            tentative = g_score[current] + move_cost + height_cost + slope_cost
            if tentative < g_score.get((nx, ny), float("inf")):
                came_from[(nx, ny)] = current
                g_score[(nx, ny)] = tentative
                heapq.heappush(open_set, (tentative + h((nx, ny), goal), (nx, ny)))

    return []


def grass_color(v: float) -> str:
    palette = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
    idx = min(4, max(0, int(round(v * 4))))
    return palette[idx]


def svg_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_svg(grid: list[list[int]]) -> str:
    heights = normalized_heights(grid)
    path = astar(heights)

    cell = 12
    gap = 3
    margin = 10
    lift = 8
    cols = len(grid[0])
    width = margin * 2 + cols * cell + (cols - 1) * gap
    height_px = margin * 2 + ROWS * cell + (ROWS - 1) * gap + lift

    def xy(x: int, y: int) -> tuple[float, float]:
        z = heights[y][x] * lift
        return margin + x * (cell + gap), margin + y * (cell + gap) - z + lift

    def center(x: int, y: int) -> tuple[float, float]:
        px, py = xy(x, y)
        return px + cell / 2, py + cell / 2

    parts: list[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height_px}" viewBox="0 0 {width} {height_px}" role="img">')
    parts.append(f'<rect width="{width}" height="{height_px}" rx="12" fill="#0d1117"/>')

    for y in range(ROWS):
        for x in range(cols):
            px, py = xy(x, y)
            color = grass_color(heights[y][x])
            opacity = 0.35 + heights[y][x] * 0.65
            parts.append(f'<rect x="{px:.2f}" y="{py:.2f}" width="{cell}" height="{cell}" rx="3" fill="{color}" opacity="{opacity:.2f}"/>')

    # contour-like edges where the height difference is large
    for y in range(ROWS):
        for x in range(cols - 1):
            if abs(heights[y][x + 1] - heights[y][x]) > 0.28:
                x1, y1 = center(x, y)
                x2, y2 = center(x + 1, y)
                parts.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="#c9d1d9" stroke-opacity="0.18" stroke-width="1"/>')
    for y in range(ROWS - 1):
        for x in range(cols):
            if abs(heights[y + 1][x] - heights[y][x]) > 0.28:
                x1, y1 = center(x, y)
                x2, y2 = center(x, y + 1)
                parts.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="#c9d1d9" stroke-opacity="0.14" stroke-width="1"/>')

    if path:
        points = " ".join(f"{center(x, y)[0]:.2f},{center(x, y)[1]:.2f}" for x, y in path)
        parts.append(f'<polyline points="{points}" fill="none" stroke="#C66D00" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')
        sx, sy = center(*path[0])
        gx, gy = center(*path[-1])
        parts.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="4" fill="#F8F8F8"/>')
        parts.append(f'<circle cx="{gx:.2f}" cy="{gy:.2f}" r="4" fill="#F8F8F8"/>')

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    counts = fetch_counts()
    grid = build_grid(counts)
    OUT.write_text(generate_svg(grid), encoding="utf-8")


if __name__ == "__main__":
    main()
