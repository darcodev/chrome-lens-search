"""The results-URL test that decides whether an upload actually landed.

Both backends ask the same question -- the req backend about ``resp.url``, the
browser about the address bar -- so they share ``_looks_like_results_url``.
These URLs are hand-written; none are captured Google responses.
"""

import pytest

import glens.lens as lens_mod
from glens.lens import _looks_like_results_url

REAL = [
    "https://www.google.com/search?udm=26&vsrid=abc&gsessionid=signed",
    "https://google.com/search?q=x&udm=26",
    "https://lens.google.com/search?vsrid=abc",
    "https://www.google.de/search?udm=26&vsrid=abc",
]
NOT_REAL = [
    # The endpoint we just POSTed to: it lives on lens.google.com, so a bare
    # "lens" substring scan read a non-redirecting upload as a results page.
    "https://lens.google.com/v3/upload",
    "https://lens.google.com/v3/upload/",
    # Interstitials carry the real target in a query param, so they matched too.
    "https://consent.google.com/m?continue=https://lens.google.com/search",
    "https://www.google.com/sorry/index?continue=https://lens.google.com/x",
    "https://www.google.com/",
    "https://shop.example/lens?udm=26",   # not a Google host at all
    "not-a-url",
    "",
]


@pytest.mark.parametrize("url", REAL)
def test_real_results_urls_are_accepted(url):
    assert _looks_like_results_url(url) is True


@pytest.mark.parametrize("url", NOT_REAL)
def test_non_results_urls_are_rejected(url):
    assert _looks_like_results_url(url) is False


def test_the_upload_endpoint_itself_is_never_a_results_url():
    # An upload that never redirects leaves resp.url sitting on the endpoint.
    assert _looks_like_results_url(lens_mod._UPLOAD_ENDPOINT) is False


def test_upload_poll_does_not_settle_on_a_consent_interstitial():
    """The address-bar poll must keep waiting through a consent hop.

    The interstitial URL carries "lens" inside its ``continue=`` param, which
    used to end the poll early and hand back the consent page as the results URL.
    """
    consent = "https://consent.google.com/m?continue=https://lens.google.com/search"
    results = "https://www.google.com/search?udm=26&vsrid=abc"

    class PollingDriver:
        """Address bar walks landing -> consent -> consent -> results."""

        def __init__(self):
            self._hops = [consent, consent, results]

        def execute_script(self, script, *args):
            pass  # the form submit itself; navigation shows up in current_url

        @property
        def current_url(self):
            return self._hops.pop(0) if len(self._hops) > 1 else self._hops[0]

    assert lens_mod._upload_to_google(PollingDriver(), b"img", 5) == results
