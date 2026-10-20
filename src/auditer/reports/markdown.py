from __future__ import annotations

from datetime import datetime, timezone

from auditer.models import AuditResult, RepoInfo


def _truncate(text: str, max_len: int) -> str:
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _format_pushed(pushed_at: str) -> str:
    return pushed_at[:10] if pushed_at else "\u2014"


def _repo_tags(repo: RepoInfo) -> str:
    tags = ""
    if repo.is_fork:
        tags += " _(fork)_"
    if repo.archived:
        tags += " _(archived)_"
    return tags


def generate_audit_report(result: AuditResult) -> str:
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stale_months = result.stale_months

    # Header
    lines.append(f"# GitHub Audit Report \u2014 `{result.account_username}` (All Repos)")
    lines.append(f"_Generated on {now}_")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|---|---|")
    lines.append(f"| Total repos | **{len(result.repos)}** |")
    lines.append(f"| Public | **{len(result.public_repos)}** |")
    lines.append(f"| Private | **{len(result.private_repos)}** |")
    lines.append(
        f"| Original repos | **{len(result.repos) - len(result.forked_repos)}** |"
    )
    lines.append(f"| Forked repos | **{len(result.forked_repos)}** |")
    lines.append(f"| Archived repos | **{len(result.archived_repos)}** |")
    lines.append(f"| Total stars | **{result.total_stars}** |")
    lines.append(f"| Total forks | **{result.total_forks}** |")
    lines.append(f"| Missing README | **{len(result.missing_readme)}** |")
    lines.append(f"| Missing License | **{len(result.missing_license)}** |")
    lines.append(f"| Missing Description | **{len(result.missing_description)}** |")
    lines.append(
        f"| Stale (>{stale_months} months) | **{len(result.stale_repos)}** |"
    )
    lines.append(f"| Average health | **{result.average_health}** |")
    lines.append(f"| Low health (<50) | **{len(result.low_health_repos)}** |")
    lines.append("")

    # Private repos table
    if result.private_repos:
        lines.append("## Private Repositories")
        lines.append("")
        lines.append(
            "| Repo | Language | Size | README | License | Description | Last Push | Stale? | Health |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")

        sorted_private = sorted(
            result.private_repos,
            key=lambda r: (r.is_stale(stale_months), r.months_inactive),
        )
        for r in sorted_private:
            readme = "Yes" if r.has_readme else "**MISSING**"
            license_val = r.license or "**MISSING**"
            desc = _truncate(r.description, 50) if r.description else "**MISSING**"
            pushed = _format_pushed(r.pushed_at)
            stale_flag = "Yes" if r.is_stale(stale_months) else "No"
            tags = _repo_tags(r)
            lang = r.language or "\u2014"
            lines.append(
                f"| [{r.name}]({r.url}){tags} | {lang} | {r.format_size()} "
                f"| {readme} | {license_val} | {desc} | {pushed} | {stale_flag} | {r.health_score()} |"
            )
        lines.append("")

    # Public repos table
    lines.append("## Public Repositories")
    lines.append("")
    lines.append(
        "| Repo | Language | Stars | Forks | README | License | Description | Last Push | Stale? | Health |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    sorted_public = sorted(result.public_repos, key=lambda r: r.stars, reverse=True)
    for r in sorted_public:
        readme = "Yes" if r.has_readme else "**MISSING**"
        license_val = r.license or "**MISSING**"
        desc = _truncate(r.description, 60) if r.description else "**MISSING**"
        pushed = _format_pushed(r.pushed_at)
        stale_flag = "Yes" if r.is_stale(stale_months) else "No"
        tags = _repo_tags(r)
        lang = r.language or "\u2014"
        lines.append(
            f"| [{r.name}]({r.url}){tags} | {lang} | {r.stars} | {r.forks} "
            f"| {readme} | {license_val} | {desc} | {pushed} | {stale_flag} | {r.health_score()} |"
        )
    lines.append("")

    # Missing sections
    if result.missing_readme:
        lines.append("## Repos Missing a README")
        lines.append("")
        for r in result.missing_readme:
            tag = " `private`" if r.private else ""
            lines.append(f"- [{r.name}]({r.url}){tag}")
        lines.append("")

    if result.missing_license:
        lines.append("## Repos Missing a License")
        lines.append("")
        for r in result.missing_license:
            tag = " `private`" if r.private else ""
            lines.append(f"- [{r.name}]({r.url}){tag}")
        lines.append("")

    if result.missing_description:
        lines.append("## Repos Missing a Description")
        lines.append("")
        for r in result.missing_description:
            tag = " `private`" if r.private else ""
            lines.append(f"- [{r.name}]({r.url}){tag}")
        lines.append("")

    # Stale repos
    if result.stale_repos:
        stale_sorted = sorted(
            result.stale_repos, key=lambda r: r.months_inactive, reverse=True
        )
        lines.append(f"## Stale Repos (no push in >{stale_months} months)")
        lines.append("")
        for r in stale_sorted:
            pushed = _format_pushed(r.pushed_at)
            tag = " `private`" if r.private else ""
            lines.append(
                f"- [{r.name}]({r.url}){tag} \u2014 last push {pushed} "
                f"({r.months_inactive} months ago)"
            )
        lines.append("")

    # Low health repos
    if result.low_health_repos:
        low_sorted = sorted(result.low_health_repos, key=lambda r: r.health_score())
        lines.append("## Low Health Repos (<50)")
        lines.append("")
        for r in low_sorted:
            tag = " `private`" if r.private else ""
            missing = r.health_missing()
            missing_str = f" — {', '.join(missing)}" if missing else ""
            lines.append(
                f"- [{r.name}]({r.url}){tag} — score **{r.health_score()}**{missing_str}"
            )
        lines.append("")

    # Pin recommendations
    lines.append("## Recommended Repos to Pin")
    lines.append("")
    lines.append(
        "_Top 6 public repos ranked by stars, forks, recency, and original (non-fork) status._"
    )
    lines.append("")

    for i, r in enumerate(result.pin_recommendations, 1):
        desc = r.description or "(no description)"
        pushed = _format_pushed(r.pushed_at)
        lines.append(
            f"{i}. **[{r.name}]({r.url})** \u2014 {r.stars} stars, "
            f"{r.forks} forks, last push {pushed}"
        )
        lines.append(f"   > {desc}")
    lines.append("")

    return "\n".join(lines)
