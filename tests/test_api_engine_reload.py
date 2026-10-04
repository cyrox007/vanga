from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import api
from src.runtime_descriptor import ModelRuntimeDescriptor


class FakeEngine:
    def __init__(self, model_path: Path):
        self.model_path = Path(model_path)
        self.db_path = Path("/tmp/imdb.duckdb")
        self.closed = False
        self.metadata = {}

    def close(self) -> None:
        self.closed = True


class ApiEngineReloadTests(unittest.TestCase):
    def setUp(self):
        api._engine = None
        api._generation = None
        api._last_reload_error = None

    def tearDown(self):
        api._engine = None
        api._generation = None
        api._last_reload_error = None

    @staticmethod
    def descriptor(generation: str) -> ModelRuntimeDescriptor:
        root = Path("/tmp") / generation
        return ModelRuntimeDescriptor(
            generation=generation,
            model_path=root / "model.cbm",
            metadata_path=root / "metadata.pkl",
            source="pointer",
        )

    def test_pointer_error_uses_already_loaded_engine(self):
        descriptor = self.descriptor("g1")
        engine = FakeEngine(descriptor.model_path)
        with mock.patch.object(
            api,
            "resolve_model_runtime_descriptor",
            return_value=descriptor,
        ), mock.patch.object(api, "KinoVanga", return_value=engine):
            self.assertIs(api._ensure_engine(), engine)

        with mock.patch.object(
            api,
            "resolve_model_runtime_descriptor",
            side_effect=FileNotFoundError("pointer повреждён"),
        ):
            self.assertIs(api._ensure_engine(), engine)

        self.assertEqual(api._generation, "g1")
        self.assertIn("pointer повреждён", str(api._last_reload_error))
        self.assertFalse(engine.closed)

    def test_failed_new_generation_keeps_previous_engine(self):
        old = FakeEngine(Path("/tmp/g1/model.cbm"))
        api._engine = old
        api._generation = "g1"
        descriptor = self.descriptor("g2")

        with mock.patch.object(
            api,
            "resolve_model_runtime_descriptor",
            return_value=descriptor,
        ), mock.patch.object(
            api,
            "KinoVanga",
            side_effect=RuntimeError("candidate load failed"),
        ):
            self.assertIs(api._ensure_engine(), old)

        self.assertEqual(api._generation, "g1")
        self.assertFalse(old.closed)
        self.assertIn("candidate load failed", str(api._last_reload_error))

    def test_descriptor_is_read_once_per_reload_attempt(self):
        descriptor = self.descriptor("g1")
        engine = FakeEngine(descriptor.model_path)
        resolver = mock.Mock(return_value=descriptor)

        with mock.patch.object(
            api,
            "resolve_model_runtime_descriptor",
            resolver,
        ), mock.patch.object(api, "KinoVanga", return_value=engine):
            self.assertIs(api._ensure_engine(), engine)

        resolver.assert_called_once_with()
        self.assertEqual(api._generation, descriptor.generation)
        self.assertEqual(engine.model_path, descriptor.model_path)

    def test_engine_session_prevents_close_until_request_finishes(self):
        state = {"descriptor": self.descriptor("g1")}
        engines = {
            "g1": FakeEngine(Path("/tmp/g1/model.cbm")),
            "g2": FakeEngine(Path("/tmp/g2/model.cbm")),
        }

        def resolve():
            return state["descriptor"]

        def make_engine(model_path):
            return engines[Path(model_path).parent.name]

        result: dict[str, object] = {}
        started = threading.Event()

        def reload_worker():
            started.set()
            result["engine"] = api._ensure_engine()

        with mock.patch.object(
            api,
            "resolve_model_runtime_descriptor",
            side_effect=resolve,
        ), mock.patch.object(api, "KinoVanga", side_effect=make_engine):
            with api._engine_session() as (engine, generation):
                self.assertIs(engine, engines["g1"])
                self.assertEqual(generation, "g1")
                state["descriptor"] = self.descriptor("g2")
                worker = threading.Thread(target=reload_worker)
                worker.start()
                self.assertTrue(started.wait(timeout=1.0))
                time.sleep(0.05)
                self.assertTrue(worker.is_alive())
                self.assertFalse(engines["g1"].closed)

            worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        self.assertTrue(engines["g1"].closed)
        self.assertIs(result.get("engine"), engines["g2"])
        self.assertEqual(api._generation, "g2")


if __name__ == "__main__":
    unittest.main()
