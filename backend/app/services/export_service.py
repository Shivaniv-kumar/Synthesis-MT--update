"""ExportService — CSV and PDF export of ActionItem lists.

- export_csv  : produces a UTF-8-BOM byte string suitable for Excel
- export_pdf  : produces PDF bytes via fpdf2 with priority colour coding
"""

from __future__ import annotations

import io
import csv
from datetime import date, datetime
from typing import Optional

from fpdf import FPDF

from app.models.action_item import ActionItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIORITY_BG: dict[str, tuple[int, int, int]] = {
    "High": (255, 230, 230),    # light red
    "Medium": (255, 255, 255),  # white
    "Low": (255, 255, 255),     # white
}
_PRIORITY_TEXT_HIGH: tuple[int, int, int] = (192, 0, 0)
_TEXT_DEFAULT: tuple[int, int, int] = (30, 30, 30)

_COL_WIDTHS = {
    "Task": 72,
    "Owner": 30,
    "Priority": 20,
    "Due Date": 24,
    "Status": 24,
}
_PAGE_W = 210  # A4 mm
_MARGIN = 10
_USABLE_W = _PAGE_W - 2 * _MARGIN


def _fmt_date(d: Optional[date]) -> str:
    if d is None:
        return ""
    return d.strftime("%Y-%m-%d")


def _fmt_datetime(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ExportService:
    """Stateless service — call methods directly without instantiation state."""

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def export_csv(
        self,
        items: list[ActionItem],
        meetings: dict[str, str],
    ) -> bytes:
        """Return a UTF-8-BOM CSV byte string.

        Args:
            items:    ActionItem ORM instances to export.
            meetings: mapping of meeting_id (str) → meeting title.

        Returns:
            bytes encoded as utf-8-sig (BOM prefix for Excel compatibility).
        """
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")

        # Header row
        writer.writerow(
            ["Task", "Owner", "Priority", "Due Date", "Status", "Context", "Meeting", "Created"]
        )

        for item in items:
            meeting_title = meetings.get(str(item.meeting_id), "")
            writer.writerow(
                [
                    item.task,
                    item.owner_label or "",
                    item.priority,
                    _fmt_date(item.due_date),
                    item.status,
                    item.context or "",
                    meeting_title,
                    _fmt_datetime(item.created_at),
                ]
            )

        return buf.getvalue().encode("utf-8-sig")

    # ------------------------------------------------------------------
    # PDF export
    # ------------------------------------------------------------------

    def export_pdf(
        self,
        items: list[ActionItem],
        title: str = "Action Items",
    ) -> bytes:
        """Return PDF bytes for the given list of ActionItems.

        Layout:
        - A4 portrait, 10 mm margins.
        - Header: title on the left, generation timestamp on the right.
        - Table: Task (wraps), Owner, Priority, Due Date, Status.
        - Colour coding: High priority rows get a light-red background;
          the Priority cell text is rendered in dark red.
        - Footer: centred page numbers.
        """

        class _PDF(FPDF):
            def __init__(self, report_title: str) -> None:
                super().__init__(orientation="P", unit="mm", format="A4")
                self._report_title = report_title
                self.set_margins(_MARGIN, _MARGIN, _MARGIN)
                self.set_auto_page_break(auto=True, margin=15)

            def header(self) -> None:  # type: ignore[override]
                self.set_font("Helvetica", "B", 13)
                self.set_text_color(*_TEXT_DEFAULT)
                self.cell(0, 8, self._report_title, ln=True, align="L")

                self.set_font("Helvetica", "", 8)
                self.set_text_color(120, 120, 120)
                stamp = f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
                self.cell(0, 5, stamp, ln=True, align="R")
                self.ln(2)

                # Draw column header row
                self._draw_table_header()

            def footer(self) -> None:  # type: ignore[override]
                self.set_y(-12)
                self.set_font("Helvetica", "I", 8)
                self.set_text_color(150, 150, 150)
                self.cell(0, 8, f"Page {self.page_no()}", align="C")

            def _draw_table_header(self) -> None:
                self.set_font("Helvetica", "B", 9)
                self.set_fill_color(50, 50, 50)
                self.set_text_color(255, 255, 255)
                self.set_draw_color(180, 180, 180)
                self.set_line_width(0.2)

                for col, w in _COL_WIDTHS.items():
                    self.cell(w, 7, col, border=1, fill=True, align="C")
                self.ln()
                self.set_text_color(*_TEXT_DEFAULT)

        pdf = _PDF(report_title=title)
        pdf.add_page()
        pdf.set_font("Helvetica", "", 9)

        row_h = 6  # base row height; task cell may expand

        for item in items:
            priority = item.priority or "Medium"
            is_high = priority == "High"

            bg = _PRIORITY_BG.get(priority, (255, 255, 255))
            pdf.set_fill_color(*bg)
            pdf.set_draw_color(180, 180, 180)
            pdf.set_line_width(0.2)

            # Calculate how many lines the task text needs at col width
            task_col_w = _COL_WIDTHS["Task"]
            # Use multi_cell for the task; we need to track Y position manually
            x_start = pdf.get_x()
            y_start = pdf.get_y()

            # Check if we have enough space; if not, add a page.
            # We estimate conservatively: at least 2 lines + margin
            if y_start + row_h * 2 > pdf.page_break_trigger:
                pdf.add_page()
                x_start = pdf.get_x()
                y_start = pdf.get_y()

            # Render the Task cell as multi_cell (wraps text)
            pdf.set_xy(x_start, y_start)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*_TEXT_DEFAULT)

            # Capture cell height by rendering off-page first
            # fpdf2 supports get_string_width for line-break estimation
            lines = pdf.multi_cell(
                w=task_col_w,
                h=row_h,
                txt=item.task,
                border=1,
                fill=True,
                split_only=True,
            )
            actual_h = max(row_h, row_h * len(lines))

            # Render the Task multi_cell
            pdf.set_xy(x_start, y_start)
            pdf.multi_cell(
                w=task_col_w,
                h=row_h,
                txt=item.task,
                border=1,
                fill=True,
                max_line_height=row_h,
            )

            # Position cursor to the right of the task cell at original Y
            # for the remaining single-height cells
            x_after_task = x_start + task_col_w

            other_cells = [
                ("Owner", _COL_WIDTHS["Owner"], item.owner_label or ""),
                (
                    "Priority",
                    _COL_WIDTHS["Priority"],
                    priority,
                ),
                ("Due Date", _COL_WIDTHS["Due Date"], _fmt_date(item.due_date)),
                ("Status", _COL_WIDTHS["Status"], item.status),
            ]

            for col_name, col_w, val in other_cells:
                pdf.set_xy(x_after_task, y_start)
                # Colour-code the priority text
                if col_name == "Priority" and is_high:
                    pdf.set_text_color(*_PRIORITY_TEXT_HIGH)
                    pdf.set_font("Helvetica", "B", 9)
                else:
                    pdf.set_text_color(*_TEXT_DEFAULT)
                    pdf.set_font("Helvetica", "", 9)

                pdf.cell(col_w, actual_h, val, border=1, fill=True, align="C")
                x_after_task += col_w

            # Advance Y past this row
            pdf.set_xy(_MARGIN, y_start + actual_h)

        # Reset styling
        pdf.set_text_color(*_TEXT_DEFAULT)
        pdf.set_font("Helvetica", "", 9)

        return bytes(pdf.output())
