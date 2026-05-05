import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger_package = types.ModuleType("brainboost_data_source_logger_package")
logger_module = types.ModuleType("brainboost_data_source_logger_package.BBLogger")


class StubBBLogger:
    @staticmethod
    def log(*args, **kwargs):
        return None


logger_module.BBLogger = StubBBLogger
logger_package.BBLogger = logger_module
sys.modules.setdefault("brainboost_data_source_logger_package", logger_package)
sys.modules.setdefault("brainboost_data_source_logger_package.BBLogger", logger_module)

config_package = types.ModuleType("brainboost_configuration_package")
config_module = types.ModuleType("brainboost_configuration_package.BBConfig")


class StubBBConfig:
    @staticmethod
    def get(*args, **kwargs):
        return None


config_module.BBConfig = StubBBConfig
config_package.BBConfig = config_module
sys.modules.setdefault("brainboost_configuration_package", config_package)
sys.modules.setdefault("brainboost_configuration_package.BBConfig", config_module)

from SubjectiveScreenshotDataSource import SubjectiveScreenshotDataSource


class ScreenshotDatasourceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.tmp_path = Path(self.temp_dir.name)
        self.datasource = SubjectiveScreenshotDataSource(
            connection={
                "default_output_folder": str(self.tmp_path / "default_out"),
                "default_file_format": "png",
            },
            config={
                "connection_name": "screenshot-test",
                "output_dir": str(self.tmp_path / "node_output"),
                "scratch_dir": str(self.tmp_path / "scratch"),
            },
        )

    def test_resolve_format_prefers_request_and_normalizes_alias(self):
        self.assertEqual(self.datasource._resolve_format({"file_format": "jpeg"}, ""), "jpg")
        self.assertEqual(self.datasource._resolve_format({}, "capture.tif"), "tiff")
        self.assertEqual(self.datasource._resolve_format({}, ""), "png")

    def test_resolve_rect_accepts_string_and_dict(self):
        self.assertEqual(self.datasource._resolve_rect("10,20,300,400"), (10, 20, 300, 400))
        self.assertEqual(
            self.datasource._resolve_rect({"x": 1, "y": 2, "width": 3, "height": 4}),
            (1, 2, 3, 4),
        )

    def test_resolve_rect_rejects_invalid_dimensions(self):
        with self.assertRaises(ValueError):
            self.datasource._resolve_rect("10,20,0,40")

    def test_resolve_output_path_uses_default_timestamp_name(self):
        frozen = "2026_03_18_12_30_45"

        class FrozenDatetime:
            @staticmethod
            def now():
                class FrozenNow:
                    @staticmethod
                    def strftime(value):
                        if value == "%Y_%m_%d_%H_%M_%S":
                            return frozen
                        raise AssertionError(value)

                return FrozenNow()

        with mock.patch("SubjectiveScreenshotDataSource.datetime", FrozenDatetime):
            output_path = self.datasource._resolve_output_path(
                output_folder=self.tmp_path,
                output_filename="",
                file_format="png",
            )
        self.assertEqual(output_path.name, f"{frozen}-screenshot.png")

    def test_write_terminal_context_reference_creates_json(self):
        screenshot_path = self.tmp_path / "image.png"
        screenshot_path.write_bytes(b"png")

        context_path = self.datasource._write_terminal_context_reference(
            screenshot_path=screenshot_path,
            file_format="png",
            monitor_number=0,
            rect=(0, 0, 100, 100),
        )

        payload = json.loads(Path(context_path).read_text(encoding="utf-8"))
        self.assertEqual(payload["path"], str(screenshot_path))
        self.assertEqual(payload["file_format"], "png")
        self.assertEqual(payload["rect"], "0,0,100,100")

    def test_enumerate_monitors_fallback_uses_imagegrab_dimensions(self):
        fake_screen = Image.new("RGB", (2560, 1440), color="black")
        with mock.patch(
            "SubjectiveScreenshotDataSource.ImageGrab.grab",
            return_value=fake_screen,
        ):
            monitors = self.datasource._enumerate_monitors_fallback()
        self.assertEqual(len(monitors), 1)
        self.assertEqual(monitors[0]["right"], 2560)
        self.assertEqual(monitors[0]["bottom"], 1440)

    def test_enumerate_monitors_on_posix_uses_fallback_not_win32(self):
        with mock.patch("os.name", "posix"):
            with mock.patch.object(
                self.datasource,
                "_enumerate_monitors_fallback",
                return_value=[
                    {
                        "left": 0,
                        "top": 0,
                        "right": 800,
                        "bottom": 600,
                        "width": 800,
                        "height": 600,
                        "primary": True,
                        "device": "Primary Monitor",
                    }
                ],
            ) as fallback:
                monitors = self.datasource._enumerate_monitors()
        fallback.assert_called_once()
        self.assertEqual(monitors[0]["width"], 800)

    def test_run_captures_and_returns_structured_result(self):
        target_path = self.tmp_path / "shot.jpg"
        fake_monitors = [
            {"left": 0, "top": 0, "right": 1920, "bottom": 1080},
            {"left": 1920, "top": 0, "right": 3840, "bottom": 1080},
        ]

        with mock.patch.object(self.datasource, "_enumerate_monitors", return_value=fake_monitors):
            with mock.patch(
                "SubjectiveScreenshotDataSource.ImageGrab.grab",
                return_value=Image.new("RGB", (1920, 1080), color="white"),
            ):
                with mock.patch.object(self.datasource, "_resolve_output_folder", lambda request: self.tmp_path):
                    result = self.datasource.run(
                        {
                            "output_filename": "shot.jpg",
                            "file_format": "jpg",
                            "compression": True,
                            "monitor_number": 1,
                        }
                    )

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["output_path"], str(target_path))
        self.assertEqual(result["monitor_count"], 2)
        self.assertEqual(result["capture_width"], 1920)
        self.assertTrue(result["compression"])
        self.assertTrue(target_path.exists())


if __name__ == "__main__":
    unittest.main()
