from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
import warnings
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient` is deprecated")

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from contracts.study_spec import BudgetState, CreditStatus
from db.repository import create_paper_run, get_run, mark_run_succeeded
from db.session import create_app_engine, create_session_factory, init_database
from ingest.batch_importer import ImportCandidate, _request_bytes, candidates_to_paper_inputs, discover_openalex
from ingest.paper_ingest import PaperIngestError, build_paper_input, extract_pdf_text
from ingest.study_loader import StudyDocument
from llm.client import LLMChatResult, LLMUsage
from pipeline.service import PipelineCancelled, PipelineConfig, execute_documents
from web.app import create_app
from web.settings import AppSettings
from worker.jobs import process_next_run


PAPER_TEXT = """Hypothesis: social norm statements improve compliance intention.
Condition control prompt: Please complete this task if you can.
Condition treatment prompt: Most people like you complete this task quickly.
The article described N = 120 participants and p-value below 0.05.
The outcome is a continuous compliance intention score."""


def _credit() -> CreditStatus:
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    return CreditStatus(
        provider="digitalocean",
        validated=True,
        expires_at=future,
        eligible_products=["app_platform"],
        blocked_products=["gpu_droplets"],
    )


class IngestWebStorageTest(unittest.TestCase):
    def test_text_ingestion_caps_and_hashes(self) -> None:
        paper = build_paper_input(
            title="Norm Study",
            pasted_text=PAPER_TEXT,
            upload_bytes=None,
            upload_filename="",
            max_chars=500,
        )
        self.assertEqual(paper.title, "Norm Study")
        self.assertEqual(paper.source_kind, "text")
        self.assertEqual(len(paper.source_sha256), 64)

    def test_blank_pdf_is_rejected_as_empty_or_scanned(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            writer.write(tmp)
            tmp.seek(0)
            with self.assertRaises(PaperIngestError):
                extract_pdf_text(tmp.read(), max_upload_mb=15, max_chars=120_000)

    def test_remote_download_is_capped_before_reading_body(self) -> None:
        class FakeResponse:
            headers = {"content-length": str(2 * 1024 * 1024)}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                return b""

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            with self.assertRaises(PaperIngestError):
                _request_bytes("https://example.org/large.pdf", max_bytes=1024 * 1024)

    def test_pipeline_is_deterministic_and_uses_unique_agents(self) -> None:
        doc = StudyDocument(
            study_id="study_a",
            title="Study A",
            source_uri="test://study-a",
            text=PAPER_TEXT,
            source_sha256="hash",
        )
        config = PipelineConfig(seed=9, max_sample_size=120)
        kwargs = {
            "docs": [doc],
            "run_id": "run_a",
            "budget_state": BudgetState(250.0, 25.0, {"extraction": 50.0, "simulation": 150.0, "analysis": 25.0, "reporting": 25.0}),
            "credits": [_credit()],
            "config": config,
        }
        first = execute_documents(**kwargs)
        second = execute_documents(**kwargs)
        self.assertEqual(first.analysis, second.analysis)
        rows = first.trials["study_a"]
        ids = [row["agent_id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("study_a", first.agents)
        self.assertEqual(len(first.agents["study_a"]), 120)
        self.assertIn("income_bucket", first.agents["study_a"][0])
        self.assertIn("methodology", first.analysis["study_a"])
        self.assertIn("matched_original_method", first.analysis["study_a"]["primary"])

    def test_pipeline_can_use_llm_agents_with_stored_responses(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls = []

            def chat_completion(self, *, messages, model, temperature, max_tokens):
                self.calls.append(messages)
                prompt = messages[-1]["content"]
                is_treatment = "Condition treatment prompt" in prompt
                score = 72 if is_treatment else 45
                return LLMChatResult(
                    content=json.dumps(
                        {
                            "response_score": score,
                            "response_label": "would comply" if is_treatment else "somewhat unsure",
                            "brief_reason": "The profile and condition imply this score.",
                        }
                    ),
                    model=model,
                    usage=LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
                )

        doc = StudyDocument(
            study_id="study_llm",
            title="Study LLM",
            source_uri="test://study-llm",
            text=PAPER_TEXT,
            source_sha256="hash-llm",
        )
        fake = FakeLLMClient()
        output = execute_documents(
            docs=[doc],
            run_id="run_llm",
            budget_state=BudgetState(250.0, 25.0, {"extraction": 50.0, "simulation": 150.0, "analysis": 25.0, "reporting": 25.0}),
            credits=[_credit()],
            config=PipelineConfig(
                seed=19,
                max_sample_size=500,
                simulation_backend="llm",
                llm_client=fake,
                llm_model="fake-digitalocean-model",
                llm_sample_size=20,
            ),
        )
        rows = output.trials["study_llm"]
        ids = [row["agent_id"] for row in rows]
        primary = output.analysis["study_llm"]["primary"]

        self.assertEqual(len(fake.calls), 20)
        self.assertEqual(len(rows), 20)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(output.extraction["items"][0]["sample_size_target"], 20)
        self.assertEqual(primary["simulation_backend"], "llm")
        self.assertEqual(primary["llm_model"], "fake-digitalocean-model")
        self.assertEqual(primary["llm_usage"]["total_tokens"], 2400)
        self.assertIn("study_llm", output.llm_responses)
        self.assertEqual(len(output.llm_responses["study_llm"]), 20)
        self.assertEqual(output.llm_responses["study_llm"][0]["model"], "fake-digitalocean-model")
        self.assertIn("digitalocean_serverless_llm_agents", output.manifest["model_tier"])

    def test_pipeline_reports_progress_and_can_cancel(self) -> None:
        doc = StudyDocument(
            study_id="study_cancel",
            title="Study Cancel",
            source_uri="test://cancel",
            text=PAPER_TEXT,
            source_sha256="hash-cancel",
        )
        events = []
        should_stop = {"value": False}

        def progress(event):
            events.append(event)
            if event.get("stage") == "simulating" and int(event.get("current", 0)) >= 50:
                should_stop["value"] = True

        with self.assertRaises(PipelineCancelled):
            execute_documents(
                docs=[doc],
                run_id="run_cancel",
                budget_state=BudgetState(250.0, 25.0, {"extraction": 50.0, "simulation": 150.0, "analysis": 25.0, "reporting": 25.0}),
                credits=[_credit()],
                config=PipelineConfig(seed=10, max_sample_size=120),
                progress_callback=progress,
                should_cancel=lambda: should_stop["value"],
            )
        self.assertTrue(any(event.get("stage") == "extracting_protocol" for event in events))
        self.assertTrue(any(event.get("stage") == "simulating" for event in events))

    def test_storage_and_worker_status_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url = f"sqlite:///{tmpdir}/runs.db"
            engine = create_app_engine(db_url)
            init_database(engine)
            sessions = create_session_factory(engine)
            settings = AppSettings(database_url=db_url, max_sample_size=120, require_credit_validation=True)
            paper = build_paper_input("Norm Study", PAPER_TEXT, None, "")

            with sessions() as session:
                run = create_paper_run(session, paper, seed=153)
                self.assertEqual(run.status, "queued")

            self.assertTrue(process_next_run(sessions, settings))

            with sessions() as session:
                out = get_run(session, run.id)
                self.assertIsNotNone(out)
                self.assertEqual(out.status, "succeeded")
                self.assertIsNotNone(out.analysis_json)
                self.assertIsNotNone(out.agents_json)
                self.assertEqual(out.progress_json["stage"], "succeeded")
                self.assertEqual(out.progress_json["percent"], 100.0)

    def test_worker_hydrates_batch_source_before_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url = f"sqlite:///{tmpdir}/hydrate.db"
            engine = create_app_engine(db_url)
            init_database(engine)
            sessions = create_session_factory(engine)
            settings = AppSettings(database_url=db_url, max_sample_size=120, require_credit_validation=True)
            fallback = build_paper_input(
                "Source Study",
                "Title: Source Study\nSource: https://example.org/paper\nImport note: Full text will be fetched by the worker before simulation.",
                None,
                "",
            )
            fallback.source_kind = "url"
            fallback.source_uri = "https://example.org/paper"

            with sessions() as session:
                run = create_paper_run(session, fallback, seed=153, budget_reservation_usd=1.26)

            with patch("worker.jobs.extract_url_text", return_value=PAPER_TEXT):
                self.assertTrue(process_next_run(sessions, settings))

            with sessions() as session:
                out = get_run(session, run.id)
                self.assertIsNotNone(out)
                self.assertEqual(out.status, "succeeded")
                self.assertIn("Condition treatment prompt", out.paper.extracted_text)

    def test_fastapi_routes_and_queue_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = AppSettings(
                database_url=f"sqlite:///{tmpdir}/web.db",
                max_sample_size=120,
                max_queued_jobs=1,
                auto_process_on_submit=False,
            )
            app = create_app(settings)
            with TestClient(app) as client:
                home = client.get("/")
                self.assertEqual(home.status_code, 200)
                self.assertIn("Replicate", home.text)

                created = client.post(
                    "/runs",
                    data={"title": "Norm Study", "paper_text": PAPER_TEXT},
                    follow_redirects=False,
                )
                self.assertEqual(created.status_code, 303)
                run_path = created.headers["location"]

                capped = client.post("/runs", data={"title": "Second", "paper_text": PAPER_TEXT})
                self.assertEqual(capped.status_code, 200)
                self.assertIn("Queue limit reached", capped.text)

                detail = client.get(run_path)
                self.assertEqual(detail.status_code, 200)
                self.assertIn("queued", detail.text)

                api = client.get("/api/runs")
                self.assertEqual(api.status_code, 200)
                self.assertEqual(len(api.json()["items"]), 1)
                self.assertIn("progress_stage", api.json()["items"][0])

                run_id = run_path.rsplit("/", 1)[-1]
                skipped = client.post(f"/runs/{run_id}/skip", follow_redirects=False)
                self.assertEqual(skipped.status_code, 303)
                skipped_api = client.get(f"/api/runs/{run_id}")
                self.assertEqual(skipped_api.json()["status"], "skipped")
                self.assertEqual(skipped_api.json()["progress"]["progress_stage"], "skipped")

                deleted = client.post(f"/runs/{run_id}/delete", follow_redirects=False)
                self.assertEqual(deleted.status_code, 303)
                after_delete = client.get("/api/runs")
                self.assertEqual(after_delete.status_code, 200)
                self.assertEqual(after_delete.json()["items"], [])
                hidden_detail = client.get(run_path)
                self.assertEqual(hidden_detail.status_code, 404)

    def test_completed_llm_run_detail_shows_study_summary(self) -> None:
        class FakeLLMClient:
            def chat_completion(self, *, messages, model, temperature, max_tokens):
                prompt = messages[-1]["content"]
                is_treatment = "Condition treatment prompt" in prompt
                return LLMChatResult(
                    content=json.dumps(
                        {
                            "response_score": 72 if is_treatment else 45,
                            "response_label": "would comply" if is_treatment else "hesitant",
                            "brief_reason": "The assigned condition changes the participant-like response.",
                        }
                    ),
                    model=model,
                    usage=LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = AppSettings(database_url=f"sqlite:///{tmpdir}/llm-summary.db", auto_process_on_submit=False)
            app = create_app(settings)
            with TestClient(app) as client:
                with app.state.session_factory() as session:
                    paper = build_paper_input("LLM Summary Study", PAPER_TEXT, None, "")
                    run = create_paper_run(session, paper, seed=153)
                    run_id = run.id

                output = execute_documents(
                    docs=[
                        StudyDocument(
                            study_id=run_id,
                            title="LLM Summary Study",
                            source_uri="test://summary",
                            text=PAPER_TEXT,
                            source_sha256="hash-summary",
                        )
                    ],
                    run_id=run_id,
                    budget_state=BudgetState(250.0, 25.0, {"extraction": 50.0, "simulation": 150.0, "analysis": 25.0, "reporting": 25.0}),
                    credits=[_credit()],
                    config=PipelineConfig(
                        seed=21,
                        max_sample_size=500,
                        simulation_backend="llm",
                        llm_client=FakeLLMClient(),
                        llm_model="fake-digitalocean-model",
                        llm_sample_size=20,
                    ),
                )

                with app.state.session_factory() as session:
                    run = get_run(session, run_id)
                    self.assertIsNotNone(run)
                    mark_run_succeeded(session, run, output.artifacts())

                detail = client.get(f"/runs/{run_id}")
                self.assertEqual(detail.status_code, 200)
                self.assertIn("LLM Study Summary", detail.text)
                self.assertIn("Abstract, method, results", detail.text)
                self.assertIn("Treatment responses were higher", detail.text)
                self.assertIn("fake-digitalocean-model", detail.text)
                self.assertIn("Stored LLM response records: 20", detail.text)
                self.assertIn("Most common response labels", detail.text)

    def test_fastapi_upload_and_budget_caps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = AppSettings(
                database_url=f"sqlite:///{tmpdir}/caps.db",
                max_upload_mb=1,
                max_total_usd=1.0,
                max_sample_size=500,
                auto_process_on_submit=False,
            )
            app = create_app(settings)
            with TestClient(app) as client:
                too_expensive = client.post("/runs", data={"title": "Norm Study", "paper_text": PAPER_TEXT})
                self.assertEqual(too_expensive.status_code, 200)
                self.assertIn("Budget cap reached", too_expensive.text)

                settings.max_total_usd = 250.0
                large_pdf = b"%PDF-" + (b"x" * (1024 * 1024 + 1))
                too_large = client.post(
                    "/runs",
                    data={"title": "Large PDF", "paper_text": ""},
                    files={"paper_pdf": ("large.pdf", large_pdf, "application/pdf")},
                )
                self.assertEqual(too_large.status_code, 200)
                self.assertIn("upload limit", too_large.text)

    def test_openalex_discovery_reconstructs_candidates(self) -> None:
        payload = {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "doi": "https://doi.org/10.123/example",
                    "display_name": "Norm Message Field Experiment",
                    "publication_year": 2024,
                    "open_access": {"oa_url": "https://example.org/paper"},
                    "primary_location": {},
                    "locations": [],
                    "abstract_inverted_index": {
                        "Hypothesis": [0],
                        "participants": [1],
                        "control": [2],
                        "treatment": [3],
                    },
                }
            ]
        }
        with patch("ingest.batch_importer._request_json", return_value=payload):
            candidates = discover_openalex("social norms", limit=1)
        self.assertEqual(len(candidates), 1)
        self.assertIn("Norm Message", candidates[0].title)
        self.assertIn("Hypothesis participants control treatment", candidates[0].text)

    def test_batch_route_queues_multiple_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = AppSettings(
                database_url=f"sqlite:///{tmpdir}/batch.db",
                max_sample_size=120,
                max_queued_jobs=50,
                max_batch_import=50,
                auto_process_on_submit=False,
            )
            app = create_app(settings)
            candidate = ImportCandidate(
                title="Batch Norm Study",
                source_uri="https://example.org/batch",
                text=PAPER_TEXT,
                source_kind="openalex",
            )

            with TestClient(app) as client:
                with patch("web.app.discover_openalex", return_value=[candidate] * 8) as mock_discover:
                    response = client.post(
                        "/batch",
                        data={"batch_query": "social norms", "batch_limit": "2", "batch_sources": ""},
                        follow_redirects=False,
                    )
                self.assertEqual(response.status_code, 303)
                self.assertGreater(mock_discover.call_args.kwargs["limit"], 2)
                self.assertIn("batch_discovered=8", response.headers["location"])
                self.assertIn("batch_suitable=8", response.headers["location"])
                api = client.get("/api/runs")
                self.assertEqual(len(api.json()["items"]), 2)


if __name__ == "__main__":
    unittest.main()
