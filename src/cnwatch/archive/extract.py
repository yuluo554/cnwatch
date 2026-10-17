"""Optional PDF text extraction: PyMuPDF, then ``pdftotext``, then nothing.

plan/04 §5.3 draws a line the code has to keep:

``ExtractionUnavailable``
    *no backend exists*. The user asked for text and the installation cannot
    produce any — a configuration problem, reported as exit 2 with an install
    hint, and nothing is written. A silent "extracted nothing" here is the
    failure mode the rule exists to prevent.
``ExtractionFailed``
    *a backend ran and this filing yielded no usable text*. The PDF is saved,
    ``text_status`` is ``unavailable``, the run still succeeds. ``data/README.md``
    S5 measured exactly this: some filings extract cleanly, leveraged-reduction
    filings come out garbled.

The excerpt is for a human reading an alert. It is never an input to
classification (plan/04 §5.3), and the classify package does not import this
module.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_WHITESPACE_RE = re.compile(r"\s+")


class ExtractionUnavailable(Exception):
    """No backend is installed. The caller reports a missing dependency."""


class ExtractionFailed(Exception):
    """A backend ran; this filing gave no usable text. Degrade, do not fail the run."""


def condense(text: str) -> str:
    """Collapse whitespace so a two-column PDF does not become 400 blank lines."""

    return _WHITESPACE_RE.sub(" ", (text or "").replace("\x00", " ")).strip()


def make_excerpt(text: str, limit: int) -> str:
    """The first ``limit`` characters of the condensed text (policy: section 4)."""

    return condense(text)[: int(limit)]


class TextExtractor:
    """Interface: ``available()`` answers the dependency question, ``extract`` the data one."""

    name = "none"

    def available(self) -> bool:  # pragma: no cover - interface
        raise RuntimeError("TextExtractor.available() must be provided")

    def extract(self, data: bytes, pages: int) -> str:  # pragma: no cover - interface
        raise RuntimeError("TextExtractor.extract() must be provided")


@dataclass(frozen=True)
class PyMuPdfExtractor(TextExtractor):
    """PyMuPDF, imported lazily so the core install stays dependency-free."""

    name = "pymupdf"

    @staticmethod
    def _module():
        try:  # PyMuPDF >= 1.24 installs the modern name
            import pymupdf  # type: ignore

            return pymupdf
        except ImportError:
            pass
        try:  # older wheels (and the 3.8-compatible range) only ship ``fitz``
            import fitz  # type: ignore

            return fitz
        except ImportError:
            return None

    def available(self) -> bool:
        return self._module() is not None

    def extract(self, data: bytes, pages: int) -> str:
        module = self._module()
        if module is None:
            raise ExtractionUnavailable(
                "PyMuPDF is not installed (pip install \"cnwatch[pdf]\")"
            )
        try:
            document = module.open(stream=data, filetype="pdf")
        except Exception as exc:  # noqa: BLE001 - a broken PDF is data, not a crash
            raise ExtractionFailed("PyMuPDF could not open the file: %s" % exc)
        try:
            chunks: List[str] = []
            for index in range(min(int(pages), document.page_count)):
                try:
                    chunks.append(document.load_page(index).get_text() or "")
                except Exception as exc:  # noqa: BLE001 - one bad page must not lose the rest
                    chunks.append("")
            return "\n".join(chunks)
        finally:
            try:
                document.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass


@dataclass(frozen=True)
class PdftotextExtractor(TextExtractor):
    """The system ``pdftotext`` (poppler), used when PyMuPDF is absent."""

    name = "pdftotext"
    binary: str = "pdftotext"
    timeout_seconds: int = 40

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _run(self, binary: str, source: Path, pages: int):
        return subprocess.run(
            [
                binary,
                "-f", "1",
                "-l", str(int(pages)),
                "-enc", "UTF-8",
                str(source),
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def extract(self, data: bytes, pages: int) -> str:
        binary = shutil.which(self.binary)
        if binary is None:
            raise ExtractionUnavailable(
                "pdftotext is not on PATH (install poppler-utils, or pip install \"cnwatch[pdf]\")"
            )
        directory = tempfile.mkdtemp(prefix="cnwatch-pdf-")
        try:
            source = Path(directory) / "announcement.pdf"
            with open(str(source), "wb") as handle:
                handle.write(data)
            completed = self._run(binary, source, pages)
        except OSError as exc:
            raise ExtractionFailed("pdftotext could not run: %s" % exc)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

        if completed.returncode != 0:
            stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
            raise ExtractionFailed(
                "pdftotext exited %d%s"
                % (completed.returncode, "" if not stderr else ": %s" % stderr.splitlines()[-1])
            )
        return (completed.stdout or b"").decode("utf-8", errors="replace")


class UnavailableExtractor(TextExtractor):
    """Neither backend exists. ``available()`` is False, so nothing is attempted."""

    name = "unavailable"

    def available(self) -> bool:
        return False

    def extract(self, data: bytes, pages: int) -> str:
        raise ExtractionUnavailable(
            "no PDF text extractor is installed; install one with "
            "pip install \"cnwatch[pdf]\" (PyMuPDF) or a system pdftotext (poppler)"
        )


def resolve_extractor(backends: Optional[List[TextExtractor]] = None) -> TextExtractor:
    """PyMuPDF first, then ``pdftotext``, then the explicit "nothing" backend."""

    for candidate in backends if backends is not None else (PyMuPdfExtractor(), PdftotextExtractor()):
        if candidate.available():
            return candidate
    return UnavailableExtractor()


INSTALL_HINT = (
    "install one of: pip install \"cnwatch[pdf]\" (PyMuPDF) or a system "
    "pdftotext from poppler-utils"
)


__all__ = [
    "INSTALL_HINT",
    "ExtractionFailed",
    "ExtractionUnavailable",
    "PdftotextExtractor",
    "PyMuPdfExtractor",
    "TextExtractor",
    "UnavailableExtractor",
    "condense",
    "make_excerpt",
    "resolve_extractor",
]
