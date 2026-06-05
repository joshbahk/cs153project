from __future__ import annotations

import pathlib
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from ingest import batch_importer
from ingest.config import IngestConfig, get_ingest_config
from ingest.discovery import arxiv, europepmc
from ingest.discovery.multi import merge_candidates
from ingest.http import HttpClient, HttpError
from ingest.suitability import assess_text
from ingest.types import ImportCandidate


FULL_METHOD_TEXT = """Introduction. We study how social norm messages change behavior.
Method. Participants (N = 240) were recruited online and randomly assigned.
Condition control prompt: Please complete this task if you can.
Condition treatment prompt: Most people like you complete this task quickly.
Procedure. Each participant read one prompt and rated compliance intention.
Results. A linear regression showed a difference with p < 0.05.
Discussion. The treatment condition increased the continuous compliance score."""

ABSTRACT_ONLY_TEXT = (
    "Title: A norms study\n"
    "Abstract:\n"
    "We examine whether social norms influence behavior in a large sample. "
    "Findings suggest meaningful associations worth further investigation."
)


class SuitabilityGateTest(unittest.TestCase):
    def test_full_method_text_is_suitable(self) -> None:
        verdict = assess_text(FULL_METHOD_TEXT, title="Norms")
        self.assertTrue(verdict.suitable)
        self.assertEqual(verdict.reason, "suitable")

    def test_abstract_only_text_is_unsuitable(self) -> None:
        verdict = assess_text(ABSTRACT_ONLY_TEXT, title="Norms")
        self.assertFalse(verdict.suitable)
        self.assertEqual(verdict.reason, "unsuitable_methodology")


class CandidateConversionTest(unittest.TestCase):
    def _abstract_candidate(self) -> ImportCandidate:
        return ImportCandidate(
            title="Norm Study",
            source_uri="https://doi.org/10.1/x",
            text=ABSTRACT_ONLY_TEXT,
            source_kind="openalex",
            doi="https://doi.org/10.1/x",
        )

    def test_full_text_candidate_is_kept(self) -> None:
        candidate = ImportCandidate(
            title="Norm Study",
            source_uri="https://doi.org/10.1/x",
            text=FULL_METHOD_TEXT,
            source_kind="openalex",
        )
        papers, report = batch_importer.resolve_candidates_to_paper_inputs([candidate], max_chars=120_000)
        self.assertEqual(len(papers), 1)
        self.assertEqual(report.created, 1)

    def test_abstract_dropped_when_resolution_disabled(self) -> None:
        with patch.dict("os.environ", {"BULK_IMPORT_RESOLVE": "0"}, clear=False):
            papers, report = batch_importer.resolve_candidates_to_paper_inputs(
                [self._abstract_candidate()], max_chars=120_000
            )
        self.assertEqual(papers, [])
        self.assertEqual(report.created, 0)
        self.assertEqual(report.dispositions[0].status, "unsuitable_methodology")

    def test_abstract_promoted_when_resolver_finds_full_text(self) -> None:
        def fake_resolver(candidate, http, config, *, max_chars):
            return FULL_METHOD_TEXT, "europepmc_fulltext"

        with patch.object(batch_importer, "resolve_full_text", side_effect=fake_resolver):
            papers, report = batch_importer.resolve_candidates_to_paper_inputs(
                [self._abstract_candidate()], max_chars=120_000
            )
        self.assertEqual(len(papers), 1)
        self.assertEqual(report.resolved_full_text, 1)
        self.assertEqual(report.dispositions[0].status, "created")
        self.assertEqual(report.dispositions[0].detail, "europepmc_fulltext")

    def test_conversion_is_one_to_one_without_dedupe(self) -> None:
        candidate = ImportCandidate(
            title="Dup",
            source_uri="https://doi.org/10.1/x",
            text=FULL_METHOD_TEXT,
            source_kind="openalex",
        )
        papers = batch_importer.candidates_to_paper_inputs([candidate, candidate], max_chars=120_000)
        self.assertEqual(len(papers), 2)


class DiscoveryAdapterTest(unittest.TestCase):
    class _FakeHttp:
        def __init__(self, *, json_payload=None, text_payload="") -> None:
            self._json = json_payload
            self._text = text_payload

        def get_json(self, url, *, params=None, headers=None):
            return self._json

        def get_text(self, url, *, max_bytes, params=None):
            return httpx.Response(200, text=self._text, request=httpx.Request("GET", url))

    def test_europepmc_parses_results(self) -> None:
        payload = {
            "resultList": {
                "result": [
                    {
                        "title": "Norm Message Trial.",
                        "abstractText": "An experiment with control and treatment conditions.",
                        "doi": "10.5/abc",
                        "pmcid": "PMC123",
                        "pubYear": "2023",
                        "source": "MED",
                        "id": "999",
                    }
                ]
            }
        }
        candidates = europepmc.search("norms", 5, self._FakeHttp(json_payload=payload))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_kind, "europepmc")
        self.assertEqual(candidates[0].doi, "10.5/abc")
        self.assertEqual(candidates[0].pmcid, "PMC123")

    def test_arxiv_parses_atom(self) -> None:
        atom = """<?xml version='1.0'?>
        <feed xmlns='http://www.w3.org/2005/Atom'>
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title>Norm Messages and Behavior</title>
            <summary>We run an experiment with treatment and control.</summary>
            <link title='pdf' href='http://arxiv.org/pdf/2401.00001v1' type='application/pdf'/>
          </entry>
        </feed>"""
        candidates = arxiv.search("norms", 5, self._FakeHttp(text_payload=atom))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_kind, "arxiv")
        self.assertEqual(candidates[0].arxiv_id, "2401.00001v1")
        self.assertTrue(candidates[0].oa_pdf_url.endswith(".pdf") or "pdf" in candidates[0].oa_pdf_url)

    def test_merge_dedupes_by_doi_and_sorts_by_relevance(self) -> None:
        low = ImportCandidate(title="A", source_uri="u1", text="t", source_kind="europepmc", doi="10.1/x", relevance=1.0)
        high = ImportCandidate(title="A", source_uri="u2", text="t", source_kind="arxiv", doi="10.1/x", relevance=9.0)
        other = ImportCandidate(title="B", source_uri="u3", text="t", source_kind="arxiv", doi="10.2/y", relevance=5.0)
        merged = merge_candidates([low, high, other])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].relevance, 9.0)


class HttpClientTest(unittest.TestCase):
    def test_retries_then_succeeds(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        client = HttpClient(max_retries=3, min_host_interval=0.0, backoff_base=0.0, transport=transport)
        try:
            data = client.get_json("https://example.org/api")
        finally:
            client.close()
        self.assertEqual(data, {"ok": True})
        self.assertEqual(attempts["n"], 3)

    def test_caps_oversized_download(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-length": str(50 * 1024 * 1024)}, content=b"x")

        transport = httpx.MockTransport(handler)
        client = HttpClient(min_host_interval=0.0, transport=transport)
        try:
            with self.assertRaises(HttpError):
                client.get_bytes("https://example.org/big.pdf", max_bytes=1024 * 1024)
        finally:
            client.close()


class ConfigTest(unittest.TestCase):
    def test_multisource_defaults_off(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            config = get_ingest_config()
        self.assertIsInstance(config, IngestConfig)
        self.assertFalse(config.multisource_discovery)
        self.assertTrue(config.resolve_full_text)

    def test_multisource_opt_in(self) -> None:
        with patch.dict("os.environ", {"BULK_IMPORT_MULTISOURCE": "1"}, clear=False):
            config = get_ingest_config()
        self.assertTrue(config.multisource_discovery)


if __name__ == "__main__":
    unittest.main()
