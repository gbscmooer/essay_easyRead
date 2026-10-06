"""Workflow-local arXiv adapter preserving exact version and date attestations."""
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .. import arxiv_client

NS = {"atom": "http://www.w3.org/2005/Atom"}


def dated_entries(xml):
    """Join each parsed document to its own Atom version, never to another entry."""
    root = ET.fromstring(xml)
    result = []
    for entry in root.findall("atom:entry", NS):
        feed = ET.Element("{" + NS["atom"] + "}feed")
        feed.append(entry)
        papers = arxiv_client._parse_entries(ET.tostring(feed, encoding="unicode"), 1)
        identity = entry.findtext("atom:id", default="", namespaces=NS)
        version = re.search(r"(\d{4}\.\d{4,5}v\d+)$", identity)
        updated = entry.findtext("atom:updated", default="", namespaces=NS)
        published = entry.findtext("atom:published", default="", namespaces=NS)
        if not papers or not version or not updated or not published:
            continue
        paper = papers[0]
        paper.update(key="arxiv:" + paper["arxiv_id"], arxiv_version=version[1],
                     pdf_url="https://arxiv.org/pdf/" + version[1],
                     publication_date=published, public_available_at=updated,
                     version_date_source="arxiv_atom")
        result.append(paper)
    return result


def before_cutoff(stamp, cutoff):
    try:
        date = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        limit = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
        return date.replace(tzinfo=date.tzinfo or timezone.utc) <= limit.replace(tzinfo=limit.tzinfo or timezone.utc)
    except (ValueError, AttributeError, TypeError):
        return False


async def version_metadata(version):
    if not re.fullmatch(r"\d{4}\.\d{4,5}v\d+", version):
        return None
    xml = await arxiv_client._arxiv_request(urllib.parse.urlencode({"id_list": version, "max_results": 1}))
    return next((p for p in dated_entries(xml) if p["arxiv_version"] == version), None)


async def search_arxiv(query, *, cutoff=None, limit=6):
    terms = re.findall(r"[\w-]+", query)
    if not terms:
        return []
    xml = await arxiv_client._arxiv_request(urllib.parse.urlencode({
        "search_query": " AND ".join("all:" + t for t in terms), "sortBy": "relevance",
        "sortOrder": "descending", "max_results": limit}))
    results = []
    for paper in dated_entries(xml):
        if cutoff and not before_cutoff(paper["public_available_at"], cutoff):
            # The first version is a conservative historical fallback, not an assertion
            # that the latest full text existed on the initial publication date.
            if not before_cutoff(paper["publication_date"], cutoff):
                continue
            paper = await version_metadata(paper["arxiv_id"] + "v1")
            if not paper or not before_cutoff(paper["public_available_at"], cutoff):
                continue
        results.append(paper)
    return results
