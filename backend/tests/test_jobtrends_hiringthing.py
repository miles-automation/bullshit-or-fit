import httpx
import pytest

from app.jobtrends.hiringthing import (
    PROVIDER_HIRINGTHING,
    HiringThingClient,
    HiringThingConfig,
    parse_hiringthing_rss,
)

CFG = HiringThingConfig(host="acme.rippling-ats.com")


def _item(job_id: str, title: str = "Software Engineer II") -> str:
    return (
        f"<item><title>{title}</title>"
        f"<link>https://acme.rippling-ats.com/job/{job_id}/software-engineer-ii</link>"
        "<description>Own features &amp;amp; ship them</description>"
        "<category/><location>Denver, CO</location></item>"
    )


def _feed(items: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0">'
        f"<channel><title>Acme Jobs</title>{items}</channel></rss>"
    )


def test_parse_maps_fields() -> None:
    job = parse_hiringthing_rss("acme", "Acme", CFG, _feed(_item("1062558")))[0]
    assert job.provider == PROVIDER_HIRINGTHING
    assert job.external_id == "1062558"
    assert job.title == "Software Engineer II"
    assert job.location == "Denver, CO"
    assert job.department is None
    assert job.content_text == "Software Engineer II · Own features & ship them"


def test_parse_accepts_an_empty_channel() -> None:
    assert parse_hiringthing_rss("acme", "Acme", CFG, _feed("")) == []


@pytest.mark.parametrize(
    "feed",
    [
        "<html>maintenance</html>",
        "not xml at all",
        '<rss version="2.0"></rss>',
        _feed("<item><title>Dev</title><link>https://x/about</link></item>"),
        _feed(_item("7", title="")),
    ],
)
def test_parse_raises_on_malformed_feed(feed: str) -> None:
    with pytest.raises(ValueError):
        parse_hiringthing_rss("acme", "Acme", CFG, feed)


def test_client_fetches_rss() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://acme.rippling-ats.com/api/rss.xml"
        return httpx.Response(200, text=_feed(_item("1") + _item("2")))

    client = HiringThingClient(transport=httpx.MockTransport(handler))
    assert len(client.fetch("acme", "Acme", CFG)) == 2


def test_client_raises_on_http_error() -> None:
    client = HiringThingClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch("acme", "Acme", CFG)
