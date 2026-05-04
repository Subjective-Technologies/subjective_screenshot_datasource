import ctypes
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageGrab, ImageOps, features
from ctypes import wintypes

from subjective_abstract_data_source_package import SubjectiveDataSource
from brainboost_data_source_logger_package.BBLogger import BBLogger


SUPPORTED_FORMATS = {
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "bmp": "bmp",
    "gif": "gif",
    "tif": "tiff",
    "tiff": "tiff",
    "webp": "webp",
}
FORMAT_EXTENSION = {
    "png": ".png",
    "jpg": ".jpg",
    "bmp": ".bmp",
    "gif": ".gif",
    "tiff": ".tiff",
    "webp": ".webp",
}
BOOLEAN_TRUE = {"true", "1", "yes", "on"}
BOOLEAN_FALSE = {"false", "0", "no", "off"}
FILENAME_SAFE_RE = re.compile(r'[^A-Za-z0-9._ -]+')


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class SubjectiveScreenshotDataSource(SubjectiveDataSource):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        conn = getattr(self, "_connection", {}) or {}
        params = self.params if isinstance(self.params, dict) else {}

        self.default_output_folder = str(
            conn.get("default_output_folder")
            or params.get("default_output_folder")
            or ""
        ).strip()
        self.default_format = self._normalize_format(
            conn.get("default_file_format")
            or params.get("default_file_format")
            or "png"
        )
        self.default_compression = self._coerce_bool(
            conn.get("default_compression")
            if "default_compression" in conn
            else params.get("default_compression"),
            default=False,
        )
        self.default_monochrome = self._coerce_bool(
            conn.get("default_monochrome")
            if "default_monochrome" in conn
            else params.get("default_monochrome"),
            default=False,
        )

    @classmethod
    def connection_schema(cls) -> dict:
        return {
            "default_output_folder": {
                "type": "folder_path",
                "label": "Default Output Folder",
                "description": "Optional default folder. If blank, the datasource writes to scratch.",
                "required": False,
                "placeholder": "Leave empty to use scratch",
            },
            "default_file_format": {
                "type": "select",
                "label": "Default File Format",
                "description": "Default image format when the request does not specify one.",
                "required": False,
                "default": "png",
                "options": ["png", "jpg", "bmp", "gif", "tiff", "webp"],
            },
            "default_compression": {
                "type": "bool",
                "label": "Default Compression",
                "description": "Enable compression-friendly encoder settings by default when the format supports them.",
                "required": False,
                "default": False,
            },
            "default_monochrome": {
                "type": "bool",
                "label": "Default Monochrome",
                "description": "Convert captures to grayscale by default.",
                "required": False,
                "default": False,
            },
        }

    @classmethod
    def request_schema(cls) -> dict:
        return {
            "output_filename": {
                "type": "text",
                "label": "Output Filename",
                "description": "Optional filename. If blank, the datasource uses YYYY_MM_DD_HH_MM_SS-screenshot.<format>.",
                "required": False,
                "placeholder": "2026_03_18_10_30_00-screenshot.png",
            },
            "output_folder": {
                "type": "folder_path",
                "label": "Output Folder",
                "description": "Optional output folder. If blank, the datasource writes to scratch.",
                "required": False,
                "placeholder": "Leave empty to use scratch",
            },
            "file_format": {
                "type": "select",
                "label": "File Format",
                "description": "Image format for the saved screenshot. WebP depends on Pillow codec support.",
                "required": False,
                "default": "png",
                "options": ["png", "jpg", "bmp", "gif", "tiff", "webp"],
            },
            "compression": {
                "type": "bool",
                "label": "Compression",
                "description": "Apply compression-friendly encoder settings when the selected format supports them.",
                "required": False,
                "default": False,
            },
            "monochrome": {
                "type": "bool",
                "label": "Monochrome",
                "description": "Convert the capture to grayscale before saving.",
                "required": False,
                "default": False,
            },
            "rect": {
                "type": "text",
                "label": "Rect",
                "description": "Optional crop rectangle as x,y,width,height relative to the selected capture area.",
                "required": False,
                "placeholder": "100,50,1280,720",
            },
            "monitor_number": {
                "type": "int",
                "label": "Monitor Number",
                "description": "Optional 1-based monitor number. Leave blank to capture all monitors.",
                "required": False,
                "min": 1,
            },
        }

    @classmethod
    def output_schema(cls) -> dict:
        return {
            "success": {"type": "bool", "label": "Success"},
            "status": {"type": "text", "label": "Status"},
            "message": {"type": "text", "label": "Message"},
            "error": {"type": "text", "label": "Error"},
            "path": {"type": "text", "label": "Path"},
            "output_path": {"type": "text", "label": "Output Path"},
            "output_filename": {"type": "text", "label": "Output Filename"},
            "output_folder": {"type": "text", "label": "Output Folder"},
            "file_format": {"type": "text", "label": "File Format"},
            "compression": {"type": "bool", "label": "Compression"},
            "monochrome": {"type": "bool", "label": "Monochrome"},
            "monitor_number": {"type": "int", "label": "Monitor Number"},
            "monitor_count": {"type": "int", "label": "Monitor Count"},
            "capture_width": {"type": "int", "label": "Capture Width"},
            "capture_height": {"type": "int", "label": "Capture Height"},
            "rect": {"type": "text", "label": "Rect"},
            "context_file_path": {"type": "text", "label": "Context File Path"},
        }

    @classmethod
    def icon(cls) -> str:
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        try:
            with open(icon_path, "r", encoding="utf-8") as handle:
                return handle.read()
        except Exception:
            return ""

    def run(self, request: dict) -> Any:
        request = self._normalize_request(request)
        result = self._empty_result()

        try:
            raw_output_filename = self._resolve_request_value(
                request,
                "output_filename",
                "output_file_name",
                "output_name",
                "filename",
            )
            file_format = self._resolve_format(request, raw_output_filename)
            output_folder = self._resolve_output_folder(request)
            output_path = self._resolve_output_path(
                output_folder=output_folder,
                output_filename=raw_output_filename,
                file_format=file_format,
            )
            compression = self._coerce_bool(
                self._resolve_request_value(request, "compression", "compress"),
                default=self.default_compression,
            )
            monochrome = self._coerce_bool(
                self._resolve_request_value(request, "monochrome", "monocrome", "grayscale"),
                default=self.default_monochrome,
            )
            monitor_number = self._resolve_monitor_number(request)
            rect = self._resolve_rect(
                self._resolve_request_value(request, "rect", "crop_rect", "capture_rect")
            )

            monitors = self._enumerate_monitors()
            base_bounds = self._select_base_bounds(monitors, monitor_number)
            capture_bounds = self._resolve_capture_bounds(base_bounds, rect)
            image = self._capture_image(capture_bounds)
            if monochrome:
                image = ImageOps.grayscale(image)
            self._save_image(image, output_path, file_format, compression)

            context_file_path = self._write_terminal_context_reference(
                screenshot_path=output_path,
                file_format=file_format,
                monitor_number=monitor_number,
                rect=rect,
            )

            result.update(
                {
                    "success": True,
                    "status": "completed",
                    "message": f"Captured screenshot: {output_path.name}",
                    "path": str(output_path),
                    "output_path": str(output_path),
                    "output_filename": output_path.name,
                    "output_folder": str(output_path.parent),
                    "file_format": file_format,
                    "compression": compression,
                    "monochrome": monochrome,
                    "monitor_number": monitor_number,
                    "monitor_count": len(monitors),
                    "capture_width": capture_bounds[2] - capture_bounds[0],
                    "capture_height": capture_bounds[3] - capture_bounds[1],
                    "rect": self._format_rect(rect),
                    "context_file_path": context_file_path,
                }
            )
            return result
        except Exception as exc:
            BBLogger.log(f"Screenshot capture failed: {exc}")
            result["status"] = "error"
            result["message"] = "Screenshot capture failed."
            result["error"] = str(exc)
            return result

    def _enumerate_monitors_fallback(self) -> list[dict[str, Any]]:
        # Use tkinter as a lightweight cross-platform way to get screen dimensions if possible
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            width = root.winfo_screenwidth()
            height = root.winfo_screenheight()
            root.destroy()
            return [{
                "left": 0, "top": 0, "right": width, "bottom": height,
                "width": width, "height": height, "primary": True, "device": "Primary Monitor"
            }]
        except Exception:
            pass
            
        # If tkinter fails, try to grab the screen with PIL to get dimensions
        try:
            img = ImageGrab.grab(all_screens=True)
            return [{
                "left": 0, "top": 0, "right": img.width, "bottom": img.height,
                "width": img.width, "height": img.height, "primary": True, "device": "Primary Monitor"
            }]
        except Exception:
            return [{
                "left": 0, "top": 0, "right": 1920, "bottom": 1080,
                "width": 1920, "height": 1080, "primary": True, "device": "Primary Monitor"
            }]

    def _enumerate_monitors(self) -> list[dict[str, Any]]:
        if os.name != "nt":
            return self._enumerate_monitors_fallback()

        self._enable_dpi_awareness()
        user32 = ctypes.windll.user32
        monitors: list[dict[str, Any]] = []
        enum_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        def callback(hmonitor, hdc, lprect, lparam):
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(info)
            if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                return True
            rect = info.rcMonitor
            monitors.append(
                {
                    "left": int(rect.left),
                    "top": int(rect.top),
                    "right": int(rect.right),
                    "bottom": int(rect.bottom),
                    "width": int(rect.right - rect.left),
                    "height": int(rect.bottom - rect.top),
                    "primary": bool(info.dwFlags & 1),
                    "device": str(info.szDevice),
                }
            )
            return True

        if not user32.EnumDisplayMonitors(0, 0, enum_proc(callback), 0):
            raise ctypes.WinError()
        if not monitors:
            raise RuntimeError("No monitors detected.")
        return monitors

    def _select_base_bounds(
        self,
        monitors: list[dict[str, Any]],
        monitor_number: int,
    ) -> tuple[int, int, int, int]:
        if monitor_number > 0:
            if monitor_number > len(monitors):
                raise ValueError(
                    f"monitor_number {monitor_number} is out of range. Connected monitors: {len(monitors)}."
                )
            monitor = monitors[monitor_number - 1]
            return monitor["left"], monitor["top"], monitor["right"], monitor["bottom"]

        left = min(monitor["left"] for monitor in monitors)
        top = min(monitor["top"] for monitor in monitors)
        right = max(monitor["right"] for monitor in monitors)
        bottom = max(monitor["bottom"] for monitor in monitors)
        return left, top, right, bottom

    def _resolve_capture_bounds(
        self,
        base_bounds: tuple[int, int, int, int],
        rect: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, int, int]:
        left, top, right, bottom = base_bounds
        if rect is None:
            return base_bounds

        crop_left = max(left, left + rect[0])
        crop_top = max(top, top + rect[1])
        crop_right = min(right, left + rect[0] + rect[2])
        crop_bottom = min(bottom, top + rect[1] + rect[3])
        if crop_right <= crop_left or crop_bottom <= crop_top:
            raise ValueError("rect does not overlap the selected capture area.")
        return crop_left, crop_top, crop_right, crop_bottom

    def _capture_image(self, capture_bounds: tuple[int, int, int, int]) -> Image.Image:
        image = ImageGrab.grab(bbox=capture_bounds, all_screens=True)
        if image is None:
            raise RuntimeError("ImageGrab did not return an image.")
        return image

    def _save_image(
        self,
        image: Image.Image,
        output_path: Path,
        file_format: str,
        compression: bool,
    ) -> None:
        save_kwargs: dict[str, Any] = {}
        pil_format = {
            "jpg": "JPEG",
            "png": "PNG",
            "bmp": "BMP",
            "gif": "GIF",
            "tiff": "TIFF",
            "webp": "WEBP",
        }[file_format]
        if file_format == "jpg":
            save_kwargs["quality"] = 75 if compression else 92
            save_kwargs["optimize"] = bool(compression)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
        elif file_format == "png":
            save_kwargs["optimize"] = bool(compression)
            if compression:
                save_kwargs["compress_level"] = 9
        elif file_format == "gif":
            save_kwargs["optimize"] = bool(compression)
        elif file_format == "tiff" and compression:
            save_kwargs["compression"] = "tiff_lzw"
        elif file_format == "webp":
            if not features.check("webp"):
                raise RuntimeError("WebP is not available in the current Pillow build.")
            save_kwargs["quality"] = 75 if compression else 90
            save_kwargs["method"] = 6 if compression else 4
            if image.mode not in {"RGB", "RGBA", "L"}:
                image = image.convert("RGB")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format=pil_format, **save_kwargs)

    def _write_terminal_context_reference(
        self,
        *,
        screenshot_path: Path,
        file_format: str,
        monitor_number: int,
        rect: tuple[int, int, int, int] | None,
    ) -> str:
        output_dir = str(getattr(self, "_config", {}).get("output_dir") or "").strip()
        if not output_dir:
            return ""

        target_dir = Path(output_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        context_path = target_dir / f"{datetime.now().strftime('%Y_%m_%d_%H_%M_%S')}-screenshot-context.json"
        with open(context_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "type": "screenshot",
                    "path": str(screenshot_path),
                    "output_path": str(screenshot_path),
                    "file_format": file_format,
                    "monitor_number": monitor_number,
                    "rect": self._format_rect(rect),
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                },
                handle,
                indent=2,
            )
        return str(context_path)

    def _resolve_output_folder(self, request: dict[str, Any]) -> Path:
        raw_output_folder = self._resolve_request_value(
            request,
            "output_folder",
            "output_dir",
            "output_directory",
            "folder",
        )
        default_root = self._default_output_root()
        if raw_output_folder in (None, ""):
            default_root.mkdir(parents=True, exist_ok=True)
            return default_root

        output_root = self._expand_path(raw_output_folder, base=default_root)
        output_root.mkdir(parents=True, exist_ok=True)
        return output_root

    def _resolve_output_path(
        self,
        *,
        output_folder: Path,
        output_filename: Any,
        file_format: str,
    ) -> Path:
        default_stem = datetime.now().strftime("%Y_%m_%d_%H_%M_%S") + "-screenshot"
        extension = FORMAT_EXTENSION[file_format]
        raw_filename = str(output_filename or "").strip()
        if not raw_filename:
            return (output_folder / f"{default_stem}{extension}").resolve()

        safe_stem = self._sanitize_filename_component(Path(Path(raw_filename).name).stem or default_stem)
        safe_stem = safe_stem or default_stem
        return (output_folder / f"{safe_stem}{extension}").resolve()

    def _resolve_format(self, request: dict[str, Any], output_filename: Any) -> str:
        raw_format = self._resolve_request_value(request, "file_format", "format", "image_format")
        if raw_format not in (None, ""):
            return self._normalize_format(raw_format)
        filename_extension = Path(str(output_filename or "")).suffix.lower().lstrip(".")
        if filename_extension:
            return self._normalize_format(filename_extension)
        return self.default_format

    def _resolve_monitor_number(self, request: dict[str, Any]) -> int:
        raw_value = self._resolve_request_value(request, "monitor_number", "monitor", "monitor_index")
        if raw_value in (None, ""):
            return 0
        monitor_number = int(raw_value)
        if monitor_number < 1:
            raise ValueError("monitor_number must be greater than or equal to 1.")
        return monitor_number

    def _resolve_rect(self, value: Any) -> tuple[int, int, int, int] | None:
        if value in (None, ""):
            return None
        if isinstance(value, dict):
            rect_values = (
                value.get("x", value.get("left")),
                value.get("y", value.get("top")),
                value.get("width", value.get("w")),
                value.get("height", value.get("h")),
            )
        elif isinstance(value, (list, tuple)) and len(value) == 4:
            rect_values = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if stripped.startswith("{"):
                return self._resolve_rect(json.loads(stripped))
            numbers = re.findall(r"-?\d+", stripped)
            if len(numbers) != 4:
                raise ValueError("rect must contain exactly four integers: x,y,width,height.")
            rect_values = tuple(int(number) for number in numbers)
        else:
            raise ValueError("rect must be a string, dict, list, or tuple.")

        x, y, width, height = (int(part) for part in rect_values)
        if width <= 0 or height <= 0:
            raise ValueError("rect width and height must be greater than zero.")
        return x, y, width, height

    def _default_output_root(self) -> Path:
        if self.default_output_folder:
            return self._expand_path(self.default_output_folder, base=self._plugin_root())
        scratch_root = self._runtime_dir("scratch_dir")
        if scratch_root is not None:
            return scratch_root
        return (self._plugin_root() / "scratch").resolve()

    def _runtime_dir(self, attr_name: str) -> Path | None:
        try:
            value = getattr(self, attr_name, "") or ""
        except Exception:
            value = ""
        if not value:
            return None
        return Path(value).expanduser().resolve()

    def _expand_path(self, raw_path: Any, base: Path | None = None) -> Path:
        path = Path(str(raw_path)).expanduser()
        if not path.is_absolute() and base is not None:
            path = base / path
        return path.resolve()

    def _normalize_format(self, value: Any) -> str:
        normalized = str(value or "").strip().lower().lstrip(".")
        if normalized not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported file format '{normalized}'. Supported formats: {', '.join(sorted(SUPPORTED_FORMATS))}."
            )
        return SUPPORTED_FORMATS[normalized]

    def _normalize_request(self, request: Any) -> dict[str, Any]:
        if request is None:
            return {}
        if isinstance(request, dict):
            return request
        if isinstance(request, str):
            return {"output_filename": request}
        raise ValueError("Screenshot request must be a dict or output filename string.")

    def _resolve_request_value(self, request: dict[str, Any], *keys: str) -> Any:
        for source in (
            request,
            getattr(self, "_connection", {}) or {},
            self.params if isinstance(self.params, dict) else {},
        ):
            for key in keys:
                value = source.get(key)
                if value not in (None, ""):
                    return value
        return None

    def _enable_dpi_awareness(self) -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    def _plugin_root(self) -> Path:
        return Path(__file__).resolve().parent

    def _sanitize_filename_component(self, value: str) -> str:
        return FILENAME_SAFE_RE.sub("_", value).strip(" ._")

    def _format_rect(self, rect: tuple[int, int, int, int] | None) -> str:
        if rect is None:
            return ""
        return ",".join(str(value) for value in rect)

    def _empty_result(self) -> dict[str, Any]:
        return {
            "success": False,
            "status": "error",
            "message": "",
            "error": "",
            "path": "",
            "output_path": "",
            "output_filename": "",
            "output_folder": "",
            "file_format": "",
            "compression": False,
            "monochrome": False,
            "monitor_number": 0,
            "monitor_count": 0,
            "capture_width": 0,
            "capture_height": 0,
            "rect": "",
            "context_file_path": "",
        }

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in BOOLEAN_TRUE:
                return True
            if normalized in BOOLEAN_FALSE:
                return False
        return bool(value)
