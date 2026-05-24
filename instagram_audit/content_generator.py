#!/usr/bin/env python3
"""
Monthly Instagram content calendar generator powered by Claude.

Generates a full month of post ideas — Reels, carousels, Stories, and captions —
then exports to a JSON file and a human-readable schedule.

Usage:
    python content_generator.py \
        --niche "fitness & nutrition" \
        --audience "women 25-35 who want to lose weight" \
        --freq 5 \
        --month 2026-06
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich import print as rprint

console = Console()


def _require_anthropic() -> Any:
    try:
        import anthropic  # type: ignore[import]
        return anthropic
    except ImportError:
        console.print(
            "[bold red]anthropic SDK not installed.[/bold red]\n"
            "Run:  pip install anthropic"
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Content types & emoji map
# ---------------------------------------------------------------------------

CONTENT_TYPES = {
    "Reel": "🎬",
    "Carousel": "📊",
    "Story": "⭕",
    "Photo": "📸",
}

POSTING_DAYS = [0, 2, 4]  # Mon, Wed, Fri by default for 3×/week

HASHTAG_STRATEGY = """
Use a tiered hashtag strategy per post:
- 2–3 niche tags   (< 50K posts) for ranking
- 3–4 medium tags  (50K–500K) for reach
- 2–3 large tags   (500K–5M) for discovery
Total: 7–10 hashtags per post (not 30 — that's outdated)
"""


# ---------------------------------------------------------------------------
# Claude-powered generators
# ---------------------------------------------------------------------------

class ContentCalendarGenerator:
    def __init__(self, api_key: str | None = None) -> None:
        anthropic = _require_anthropic()
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            console.print(
                "[red]ANTHROPIC_API_KEY not set. Export it or pass --api-key.[/red]"
            )
            sys.exit(1)
        self.client = anthropic.Anthropic(api_key=key)

    def _call_claude(self, prompt: str, max_tokens: int = 2048) -> str:
        message = self.client.messages.create(
            model="claude-opus-4-7",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    # ------------------------------------------------------------------
    # Individual generators
    # ------------------------------------------------------------------

    def generate_reel_idea(self, niche: str, audience: str, topic: str) -> dict[str, str]:
        prompt = f"""
You are an expert Instagram Reels strategist.

Niche: {niche}
Target audience: {audience}
Topic area: {topic}

Generate ONE Reel idea with:
1. Hook (first 3 seconds on-screen text, max 8 words, must create curiosity or shock)
2. Script outline (5–7 bullet points, 30–60 sec total)
3. Trending audio suggestion (describe the vibe, not a specific song)
4. CTA (call-to-action for the caption)

Return ONLY a JSON object with keys: hook, script_bullets (list), audio_vibe, cta
"""
        raw = self._call_claude(prompt, max_tokens=600)
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            return {"hook": raw[:100], "script_bullets": [], "audio_vibe": "", "cta": ""}

    def generate_carousel_idea(self, niche: str, audience: str, topic: str) -> dict[str, str]:
        prompt = f"""
You are an expert Instagram carousel strategist.

Niche: {niche}
Target audience: {audience}
Topic area: {topic}

Generate ONE carousel post with:
1. Title slide text (headline, max 8 words)
2. Slide 2–7 headings (one insight per slide, punchy)
3. Last slide CTA (save-worthy close)
4. Caption hook (first line before "more", creates curiosity)

Return ONLY a JSON object with keys: title, slides (list of strings), last_slide_cta, caption_hook
"""
        raw = self._call_claude(prompt, max_tokens=600)
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            return {"title": "", "slides": [], "last_slide_cta": "", "caption_hook": raw[:100]}

    def generate_caption(
        self, niche: str, audience: str, post_type: str, topic: str, cta: str = ""
    ) -> str:
        prompt = f"""
Write an Instagram caption for a {post_type} post.

Niche: {niche}
Audience: {audience}
Topic: {topic}
CTA hint: {cta or "save this post / comment below"}

Rules:
- First line: strong hook (question, bold statement, or "X things you didn't know…")
- Body: 3–5 short paragraphs, conversational, no fluff
- End with a direct question to drive comments
- Max 300 words
- DO NOT include hashtags (they are added separately)

Return ONLY the caption text, no extra commentary.
"""
        return self._call_claude(prompt, max_tokens=500).strip()

    def generate_hashtag_set(self, niche: str, post_type: str) -> list[str]:
        prompt = f"""
Generate a hashtag set for an Instagram {post_type} post in the "{niche}" niche.

Rules:
- 8–10 hashtags total
- Mix: 3 niche (<50K), 3 medium (50K–500K), 2–3 large (500K–5M)
- No lifestyle generics like #instagood or #photooftheday
- All lowercase, no spaces

Return ONLY a JSON array of hashtag strings (with the # symbol).
"""
        raw = self._call_claude(prompt, max_tokens=300)
        try:
            start = raw.index("[")
            end = raw.rindex("]") + 1
            return json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            tags = [w.strip() for w in raw.split() if w.startswith("#")]
            return tags[:10]

    def generate_story_sequence(self, niche: str, audience: str, theme: str) -> list[str]:
        prompt = f"""
You are an Instagram Stories strategist.

Niche: {niche}
Audience: {audience}
Theme for this week: {theme}

Generate a 5-slide Story sequence designed to drive profile visits.

Each slide should be described in one sentence (what's on screen + any interactive element).
Use: polls, questions, countdowns, or link stickers where relevant.

Return ONLY a JSON array of 5 strings.
"""
        raw = self._call_claude(prompt, max_tokens=400)
        try:
            start = raw.index("[")
            end = raw.rindex("]") + 1
            return json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            return [raw[:200]]

    # ------------------------------------------------------------------
    # Monthly calendar
    # ------------------------------------------------------------------

    def _posting_dates(self, year: int, month: int, freq: int) -> list[date]:
        """Return `freq` evenly distributed posting dates within the month."""
        _, days_in_month = monthrange(year, month)
        all_dates = [date(year, month, d) for d in range(1, days_in_month + 1)]

        if freq >= days_in_month:
            return all_dates

        step = days_in_month / freq
        selected: list[date] = []
        for i in range(freq):
            idx = min(int(i * step), days_in_month - 1)
            selected.append(all_dates[idx])
        return selected

    def generate_monthly_calendar(
        self,
        niche: str,
        audience: str,
        month_str: str,
        freq: int = 4,
        story_freq: int = 3,
    ) -> dict[str, Any]:
        year, month = (int(x) for x in month_str.split("-"))
        posting_dates = self._posting_dates(year, month, freq)

        # Generate content-type rotation: Reel, Carousel, Photo, Carousel, Reel …
        rotation = ["Reel", "Carousel", "Photo", "Carousel"]

        # Weekly story themes
        story_themes = [
            "behind-the-scenes",
            "quick tips",
            "audience Q&A",
            "myth-busting",
        ]

        posts: list[dict[str, Any]] = []

        with console.status("Generating monthly content calendar with Claude …"):
            for i, post_date in enumerate(posting_dates):
                ptype = rotation[i % len(rotation)]
                topic = f"{niche} tip #{i + 1}"

                post: dict[str, Any] = {
                    "date": post_date.isoformat(),
                    "type": ptype,
                    "topic": topic,
                }

                if ptype == "Reel":
                    post["reel"] = self.generate_reel_idea(niche, audience, topic)
                    cta = post["reel"].get("cta", "")
                elif ptype == "Carousel":
                    post["carousel"] = self.generate_carousel_idea(niche, audience, topic)
                    cta = post["carousel"].get("last_slide_cta", "")
                else:
                    cta = ""

                post["caption"] = self.generate_caption(niche, audience, ptype, topic, cta)
                post["hashtags"] = self.generate_hashtag_set(niche, ptype)

                console.print(f"  [green]✓[/green] {post_date}  {CONTENT_TYPES[ptype]} {ptype}")
                posts.append(post)

        # Story sequences: one per week
        stories: list[dict[str, Any]] = []
        story_dates = self._posting_dates(year, month, story_freq)
        for j, story_date in enumerate(story_dates):
            theme = story_themes[j % len(story_themes)]
            slides = self.generate_story_sequence(niche, audience, theme)
            stories.append({
                "date": story_date.isoformat(),
                "theme": theme,
                "slides": slides,
            })
            console.print(f"  [cyan]✓[/cyan] {story_date}  ⭕ Story ({theme})")

        return {
            "month": month_str,
            "niche": niche,
            "target_audience": audience,
            "posting_frequency_per_month": freq,
            "posts": posts,
            "stories": stories,
            "hashtag_strategy_note": HASHTAG_STRATEGY.strip(),
        }

    # ------------------------------------------------------------------
    # Export & print
    # ------------------------------------------------------------------

    def save_calendar(self, calendar: dict[str, Any], path: str = "content_calendar.json") -> None:
        Path(path).write_text(json.dumps(calendar, indent=2, ensure_ascii=False))
        console.print(f"\n[bold green]Calendar saved to {path}[/bold green]")

    def print_schedule(self, calendar: dict[str, Any]) -> None:
        console.rule(f"[bold blue]Content Calendar — {calendar['month']}[/bold blue]")
        rprint(f"Niche: [bold]{calendar['niche']}[/bold]  |  Audience: {calendar['target_audience']}")

        t = Table(title="Feed Posts", show_lines=True)
        t.add_column("Date", style="dim")
        t.add_column("Type")
        t.add_column("Caption hook")
        t.add_column("Hashtags (preview)")

        for post in calendar["posts"]:
            emoji = CONTENT_TYPES.get(post["type"], "")
            caption_lines = post.get("caption", "").split("\n")
            hook = caption_lines[0][:60] if caption_lines else ""
            htags = " ".join(post.get("hashtags", [])[:4]) + " …"
            t.add_row(post["date"], f"{emoji} {post['type']}", hook, htags)

        console.print(t)

        if calendar.get("stories"):
            t2 = Table(title="Story Sequences")
            t2.add_column("Date", style="dim")
            t2.add_column("Theme")
            t2.add_column("Slide 1")
            for s in calendar["stories"]:
                t2.add_row(
                    s["date"],
                    s["theme"],
                    s["slides"][0][:70] if s.get("slides") else "",
                )
            console.print(t2)

        console.print("\n[bold]Posting tips:[/bold]")
        rprint(calendar.get("hashtag_strategy_note", ""))
        console.rule()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Instagram content calendar generator")
    parser.add_argument("--niche", "-n", required=True, help='Your content niche, e.g. "home cooking"')
    parser.add_argument("--audience", "-a", required=True, help='Target audience description')
    parser.add_argument("--month", "-m", default=None, help="Month to generate for, e.g. 2026-06 (default: next month)")
    parser.add_argument("--freq", type=int, default=12, help="Feed posts per month (default 12)")
    parser.add_argument("--story-freq", type=int, default=4, help="Story sequences per month (default 4)")
    parser.add_argument("--api-key", default=None, help="Anthropic API key (or set ANTHROPIC_API_KEY)")
    parser.add_argument("--output", default="content_calendar.json", help="Output file path")
    args = parser.parse_args()

    if args.month is None:
        today = date.today()
        first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        args.month = first_next.strftime("%Y-%m")

    gen = ContentCalendarGenerator(api_key=args.api_key)
    calendar = gen.generate_monthly_calendar(
        niche=args.niche,
        audience=args.audience,
        month_str=args.month,
        freq=args.freq,
        story_freq=args.story_freq,
    )
    gen.save_calendar(calendar, args.output)
    gen.print_schedule(calendar)


if __name__ == "__main__":
    main()
