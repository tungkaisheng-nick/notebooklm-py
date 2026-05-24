#!/usr/bin/env python3
"""
Instagram follower-growth audit tool.

Logs into your account (session cached to avoid repeated logins),
fetches your last N posts, and produces a structured report that
diagnoses why follower growth may have stalled.

Usage:
    python audit.py --username YOUR_USERNAME --posts 30
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

console = Console()

SESSION_FILE = Path(".ig_session.json")


# ---------------------------------------------------------------------------
# Lazy import so the error message is friendly if instagrapi isn't installed
# ---------------------------------------------------------------------------
def _require_instagrapi() -> Any:
    try:
        from instagrapi import Client  # type: ignore[import]
        return Client
    except ImportError:
        console.print(
            "[bold red]instagrapi is not installed.[/bold red]\n"
            "Run:  pip install instagrapi"
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engagement_rate(likes: int, comments: int, followers: int) -> float:
    """(likes + comments) / followers * 100, capped at 100."""
    if followers == 0:
        return 0.0
    return min((likes + comments) / followers * 100, 100.0)


def _categorise_hashtag(tag: str, tag_counts: dict[str, int]) -> str:
    """Bucket a hashtag into size tiers based on post count."""
    n = tag_counts.get(tag, 0)
    if n == 0:
        return "unknown"
    if n < 10_000:
        return "niche (<10K)"
    if n < 100_000:
        return "small (10K–100K)"
    if n < 1_000_000:
        return "medium (100K–1M)"
    if n < 10_000_000:
        return "large (1M–10M)"
    return "mega (>10M)"


def _weekday_name(n: int) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][n]


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------

class InstagramAuditor:
    def __init__(self, username: str, password: str | None = None) -> None:
        Client = _require_instagrapi()
        self.client = Client()
        self.username = username
        self._login(password)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _login(self, password: str | None) -> None:
        if SESSION_FILE.exists():
            try:
                self.client.load_settings(str(SESSION_FILE))
                self.client.login(self.username, password or "")
                console.print("[green]Reused cached session.[/green]")
                return
            except Exception:
                SESSION_FILE.unlink(missing_ok=True)

        if not password:
            import getpass
            password = getpass.getpass(f"Password for @{self.username}: ")

        console.print(f"Logging in as [bold]@{self.username}[/bold] …")
        self.client.login(self.username, password)
        self.client.dump_settings(str(SESSION_FILE))
        console.print("[green]Login successful. Session cached.[/green]")

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _user_info(self) -> Any:
        return self.client.user_info_by_username(self.username)

    def _fetch_posts(self, n: int) -> list[Any]:
        user_id = self.client.user_id_from_username(self.username)
        return self.client.user_medias(user_id, amount=n)

    # ------------------------------------------------------------------
    # Analysis sections
    # ------------------------------------------------------------------

    def profile_health(self, info: Any) -> dict[str, Any]:
        issues: list[str] = []
        score = 100

        if not info.biography:
            issues.append("No bio — add a keyword-rich bio (max 150 chars)")
            score -= 20
        elif len(info.biography) < 60:
            issues.append("Bio is very short — expand to describe your niche clearly")
            score -= 10

        if not info.external_url:
            issues.append("No link in bio — add a Linktree or landing page")
            score -= 15

        if not info.profile_pic_url:
            issues.append("No profile picture")
            score -= 15

        if not info.is_business:
            issues.append(
                "Personal account — switch to Creator/Business for analytics access"
            )
            score -= 10

        ratio = info.follower_count / max(info.following_count, 1)
        if ratio < 0.5:
            issues.append(
                f"Following {info.following_count} but only {info.follower_count} followers "
                f"(ratio {ratio:.2f}) — unfollow inactive/irrelevant accounts"
            )
            score -= 10

        return {
            "score": max(score, 0),
            "followers": info.follower_count,
            "following": info.following_count,
            "posts": info.media_count,
            "is_verified": info.is_verified,
            "is_business": info.is_business,
            "has_bio": bool(info.biography),
            "has_link": bool(info.external_url),
            "issues": issues,
        }

    def content_analysis(self, posts: list[Any], followers: int) -> dict[str, Any]:
        if not posts:
            return {"error": "No posts found"}

        type_counts: Counter[str] = Counter()
        engagement_by_type: dict[str, list[float]] = defaultdict(list)
        engagement_by_weekday: dict[int, list[float]] = defaultdict(list)
        engagement_by_hour: dict[int, list[float]] = defaultdict(list)
        all_hashtags: list[str] = []
        top_posts: list[dict[str, Any]] = []

        for post in posts:
            likes = getattr(post, "like_count", 0) or 0
            comments = getattr(post, "comment_count", 0) or 0
            er = _engagement_rate(likes, comments, followers)

            media_type = str(getattr(post, "media_type", 1))
            product_type = getattr(post, "product_type", "") or ""
            if media_type == "2" or product_type == "clips":
                ptype = "Reel"
            elif media_type == "8":
                ptype = "Carousel"
            else:
                ptype = "Photo"

            type_counts[ptype] += 1
            engagement_by_type[ptype].append(er)

            taken_at: datetime = post.taken_at
            if taken_at.tzinfo is None:
                taken_at = taken_at.replace(tzinfo=timezone.utc)
            engagement_by_weekday[taken_at.weekday()].append(er)
            engagement_by_hour[taken_at.hour].append(er)

            caption = getattr(post, "caption_text", "") or ""
            tags = [w.lstrip("#") for w in caption.split() if w.startswith("#")]
            all_hashtags.extend(tags)

            top_posts.append(
                {
                    "type": ptype,
                    "likes": likes,
                    "comments": comments,
                    "engagement_rate": round(er, 2),
                    "date": taken_at.strftime("%Y-%m-%d"),
                    "caption_preview": caption[:80].replace("\n", " "),
                }
            )

        top_posts.sort(key=lambda x: x["engagement_rate"], reverse=True)

        # Best weekday / hour
        best_weekday = max(
            engagement_by_weekday, key=lambda d: sum(engagement_by_weekday[d]) / len(engagement_by_weekday[d])
        ) if engagement_by_weekday else None

        best_hour = max(
            engagement_by_hour, key=lambda h: sum(engagement_by_hour[h]) / len(engagement_by_hour[h])
        ) if engagement_by_hour else None

        avg_by_type = {
            t: round(sum(ers) / len(ers), 2)
            for t, ers in engagement_by_type.items()
        }
        overall_er = round(
            sum(p["engagement_rate"] for p in top_posts) / len(top_posts), 2
        )

        return {
            "total_posts_analysed": len(posts),
            "content_mix": dict(type_counts),
            "avg_engagement_rate_by_type": avg_by_type,
            "overall_avg_engagement_rate": overall_er,
            "best_posting_weekday": _weekday_name(best_weekday) if best_weekday is not None else "N/A",
            "best_posting_hour_utc": f"{best_hour:02d}:00 UTC" if best_hour is not None else "N/A",
            "top_5_posts": top_posts[:5],
            "worst_5_posts": top_posts[-5:],
            "hashtag_count_per_post": round(len(all_hashtags) / len(posts), 1),
            "most_used_hashtags": [tag for tag, _ in Counter(all_hashtags).most_common(10)],
        }

    def diagnose_growth_blockers(
        self, profile: dict[str, Any], content: dict[str, Any]
    ) -> list[dict[str, str]]:
        blockers: list[dict[str, str]] = []

        er = content.get("overall_avg_engagement_rate", 0)
        if er < 1.0:
            blockers.append({
                "severity": "HIGH",
                "area": "Engagement",
                "problem": f"Average engagement rate is {er}% — below the 1% healthy threshold.",
                "fix": (
                    "Focus on hook quality in the first 3 seconds of Reels and first line of captions. "
                    "End every post with a direct call-to-action question."
                ),
            })
        elif er < 3.0:
            blockers.append({
                "severity": "MEDIUM",
                "area": "Engagement",
                "problem": f"Engagement rate {er}% is moderate. Instagram's algorithm deprioritises low-save content.",
                "fix": "Create more saveable content: tutorials, checklists, how-to carousels.",
            })

        mix = content.get("content_mix", {})
        reel_count = mix.get("Reel", 0)
        total = sum(mix.values()) or 1
        if reel_count / total < 0.4:
            blockers.append({
                "severity": "HIGH",
                "area": "Content mix",
                "problem": f"Only {reel_count}/{total} posts are Reels ({reel_count/total*100:.0f}%). "
                           "Reels have ~3× the reach of static posts.",
                "fix": "Aim for at least 50–60% Reels in your content mix.",
            })

        htags = content.get("hashtag_count_per_post", 0)
        if htags == 0:
            blockers.append({
                "severity": "HIGH",
                "area": "Hashtags",
                "problem": "No hashtags detected — your content has no discoverability signal.",
                "fix": "Use 5–10 targeted hashtags per post (mix of niche, medium, and large).",
            })
        elif htags > 20:
            blockers.append({
                "severity": "MEDIUM",
                "area": "Hashtags",
                "problem": f"Average {htags} hashtags per post — this can look spammy.",
                "fix": "Reduce to 5–10 highly relevant hashtags.",
            })

        for issue in profile.get("issues", []):
            blockers.append({
                "severity": "MEDIUM",
                "area": "Profile",
                "problem": issue,
                "fix": "Fix profile completeness to improve first impressions and trust.",
            })

        followers = profile.get("followers", 0)
        posts = profile.get("posts", 0)
        if followers > 0 and posts / max(followers / 1000, 1) < 1:
            blockers.append({
                "severity": "MEDIUM",
                "area": "Posting frequency",
                "problem": "Posting frequency appears low relative to your audience size.",
                "fix": "Post at least 3–5× per week consistently. Consistency signals account health to the algorithm.",
            })

        return blockers

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def run_audit(self, n_posts: int = 30) -> dict[str, Any]:
        with console.status("Fetching profile …"):
            info = self._user_info()

        with console.status(f"Fetching last {n_posts} posts …"):
            posts = self._fetch_posts(n_posts)

        console.print(f"[cyan]Analysing {len(posts)} posts …[/cyan]")

        profile = self.profile_health(info)
        content = self.content_analysis(posts, profile["followers"])
        blockers = self.diagnose_growth_blockers(profile, content)

        return {
            "username": self.username,
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "profile_health": profile,
            "content_analysis": content,
            "growth_blockers": blockers,
        }

    def print_report(self, report: dict[str, Any]) -> None:
        console.rule("[bold blue]Instagram Growth Audit Report[/bold blue]")

        # --- Profile ---
        ph = report["profile_health"]
        rprint(Panel(
            f"[bold]@{report['username']}[/bold]\n"
            f"Followers: {ph['followers']:,}  |  Following: {ph['following']:,}  "
            f"|  Posts: {ph['posts']}\n"
            f"Business account: {'Yes' if ph['is_business'] else 'No'}  |  "
            f"Verified: {'Yes' if ph['is_verified'] else 'No'}\n"
            f"Has bio: {'✓' if ph['has_bio'] else '✗'}  |  "
            f"Has link: {'✓' if ph['has_link'] else '✗'}\n"
            f"[bold]Profile health score: {ph['score']}/100[/bold]",
            title="Profile", border_style="blue"
        ))

        # --- Content mix ---
        ca = report["content_analysis"]
        t = Table(title="Content Mix & Engagement")
        t.add_column("Type"); t.add_column("Count"); t.add_column("Avg ER %")
        for ptype, cnt in ca.get("content_mix", {}).items():
            er = ca.get("avg_engagement_rate_by_type", {}).get(ptype, 0)
            t.add_row(ptype, str(cnt), f"{er}%")
        console.print(t)

        rprint(
            f"\nOverall avg engagement rate: [bold]{ca['overall_avg_engagement_rate']}%[/bold]  "
            f"(healthy > 3%)\n"
            f"Best day to post: [bold]{ca['best_posting_weekday']}[/bold]  |  "
            f"Best time: [bold]{ca['best_posting_hour_utc']}[/bold]\n"
            f"Avg hashtags/post: [bold]{ca['hashtag_count_per_post']}[/bold]"
        )

        # --- Top posts ---
        t2 = Table(title="Top 5 Posts by Engagement")
        t2.add_column("Date"); t2.add_column("Type"); t2.add_column("ER %")
        t2.add_column("Likes"); t2.add_column("Comments"); t2.add_column("Caption")
        for p in ca.get("top_5_posts", []):
            t2.add_row(
                p["date"], p["type"], str(p["engagement_rate"]),
                str(p["likes"]), str(p["comments"]), p["caption_preview"]
            )
        console.print(t2)

        # --- Growth blockers ---
        console.rule("[bold red]Growth Blockers[/bold red]")
        blockers = report["growth_blockers"]
        if not blockers:
            console.print("[green]No critical blockers found![/green]")
        for b in blockers:
            colour = "red" if b["severity"] == "HIGH" else "yellow"
            rprint(
                f"\n[{colour}][{b['severity']}] {b['area']}[/{colour}]\n"
                f"Problem: {b['problem']}\n"
                f"Fix: [italic]{b['fix']}[/italic]"
            )

        console.rule()
        rprint(f"Full JSON report saved to [bold]audit_report.json[/bold]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Instagram follower-growth auditor")
    parser.add_argument("--username", "-u", required=True, help="Your Instagram username")
    parser.add_argument("--password", "-p", default=None, help="Instagram password (prompted if omitted)")
    parser.add_argument("--posts", type=int, default=30, help="Number of recent posts to analyse (default 30)")
    parser.add_argument("--json-only", action="store_true", help="Print JSON report and exit")
    args = parser.parse_args()

    auditor = InstagramAuditor(args.username, args.password)
    report = auditor.run_audit(args.posts)

    Path("audit_report.json").write_text(json.dumps(report, indent=2, default=str))

    if args.json_only:
        print(json.dumps(report, indent=2, default=str))
    else:
        auditor.print_report(report)


if __name__ == "__main__":
    main()
