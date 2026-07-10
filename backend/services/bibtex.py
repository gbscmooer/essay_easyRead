"""BibTeX formatting helpers for paper metadata."""

from __future__ import annotations

import re


def _escape(value: object) -> str:
    text = str(value or "").strip()
    return (
        text.replace("\\", "\\textbackslash{}")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("&", "\\&")
    )


def _authors(authors: str) -> str:
    parts = [
        part.strip()
        for part in re.split(r"\s*(?:;|\band\b|,)\s*", authors or "")
        if part.strip()
    ]
    return " and ".join(parts)


def _key(title: str, authors: str, year: int | None) -> str:
    first_author = _authors(authors).split(" and ")[0].strip()
    author_parts = re.findall(r"[A-Za-z0-9]+", first_author)
    author_token = (author_parts[-1] if author_parts else "paper").lower()

    stop_words = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
    title_parts = [
        part.lower()
        for part in re.findall(r"[A-Za-z0-9]+", title)
        if part.lower() not in stop_words
    ]
    title_token = title_parts[0] if title_parts else "paper"
    year_token = str(year) if year else "nd"
    return f"{author_token}{year_token}{title_token}"


def _entry_type(venue: str) -> str:
    conference_markers = {
        "AAAI", "ACL", "BMVC", "COLT", "CVPR", "ECCV", "EMNLP", "ICCV",
        "ICLR", "ICML", "IJCAI", "KDD", "MICCAI", "NAACL", "NeurIPS",
        "NIPS", "SIGGRAPH", "UAI", "WACV",
    }
    upper_venue = (venue or "").upper()
    if any(marker.upper() in upper_venue for marker in conference_markers):
        return "inproceedings"
    return "article"


def format_bibtex(title: str, authors: str = "", venue: str = "", year: int | None = None) -> str:
    """Format available paper metadata as a compact BibTeX entry."""

    entry_type = _entry_type(venue)
    venue_field = "booktitle" if entry_type == "inproceedings" else "journal"
    fields = [("title", title)]
    formatted_authors = _authors(authors)
    if formatted_authors:
        fields.append(("author", formatted_authors))
    if venue:
        fields.append((venue_field, venue))
    if year:
        fields.append(("year", str(year)))

    lines = [f"@{entry_type}{{{_key(title, authors, year)},"]
    for index, (name, value) in enumerate(fields):
        suffix = "," if index < len(fields) - 1 else ""
        lines.append(f"  {name} = {{{_escape(value)}}}{suffix}")
    lines.append("}")
    return "\n".join(lines) + "\n"
