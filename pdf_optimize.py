"""PDF optimization for e-readers (pdf2kobo approach).

Splits each page into top/bottom halves with slight overlap,
interleaves them, and rotates -90° for landscape reading.
Uses pdftocairo (poppler-utils) for splitting and pypdf for assembly.
"""

import logging
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation
from result import Err, Ok, Result

log = logging.getLogger(__name__)

OVERLAP = 0.97  # slight overlap so cut lines repeat


def optimize(input_path: Path) -> Result[Path, str]:
    """Split pages top/bottom, interleave, rotate for landscape. Returns output path."""
    output_path = input_path.parent / f"{input_path.stem}-kobo.pdf"

    try:
        reader = PdfReader(str(input_path))
        writer = PdfWriter()

        for page in reader.pages:
            box = page.mediabox
            width = float(box.width)
            height = float(box.height)
            cut_at = height / 2 * OVERLAP

            # Upper half
            upper = page.clone(writer)
            upper.mediabox.lower_left = (0, height - cut_at)
            upper.mediabox.upper_right = (width, height)
            upper.add_transformation(Transformation().rotate(-90))
            upper.mediabox.lower_left = (0, 0)
            upper.mediabox.upper_right = (cut_at, width)
            writer.add_page(upper)

            # Lower half
            lower = page.clone(writer)
            lower.mediabox.lower_left = (0, 0)
            lower.mediabox.upper_right = (width, cut_at)
            lower.add_transformation(Transformation().rotate(-90))
            lower.mediabox.lower_left = (0, 0)
            lower.mediabox.upper_right = (cut_at, width)
            writer.add_page(lower)

        with open(output_path, "wb") as f:
            writer.write(f)

        log.info(
            "Optimized PDF: %s -> %s (%d pages)",
            input_path.name,
            output_path.name,
            len(writer.pages),
        )
        return Ok(output_path)

    except Exception as e:
        log.error("PDF optimization failed for %s: %s", input_path.name, e)
        return Err(f"PDF optimization failed: {e}")
