"""Read MyFinanda XLSX with the Python standard library only (no openpyxl needed
for reading), and write CSV / optional XLSX output.

An .xlsx file is a zip of XML parts. We resolve shared strings and inline
strings, and return each sheet as an ordered list of row dicts keyed by the
header text of the first row.
"""

from __future__ import annotations

import csv
import datetime as _dt
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NSR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Excel's 1900 date system, corrected for the fictional 1900-02-29.
_EXCEL_EPOCH = _dt.date(1899, 12, 30)


@dataclass
class Sheet:
    name: str
    headers: list[str]
    rows: list[dict[str, str]] = field(default_factory=list)


def _col_letters(ref: str) -> str:
    out = []
    for ch in ref:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(out)


def _col_key(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def _read_shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        raw = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    strings: list[str] = []
    for si in root:
        strings.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    return strings


def _cell_value(c: ET.Element, shared: list[str]) -> str:
    ctype = c.get("t")
    v = c.find(_NS + "v")
    is_el = c.find(_NS + "is")
    if ctype == "s" and v is not None:
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError, TypeError):
            return ""
    if is_el is not None:
        return "".join(t.text or "" for t in is_el.iter(_NS + "t"))
    if v is not None and v.text is not None:
        return v.text
    return ""


def read_workbook(path: str) -> list[Sheet]:
    """Return sheets in workbook order. First row of each sheet is the header."""
    z = zipfile.ZipFile(path)
    shared = _read_shared_strings(z)

    wb = ET.fromstring(z.read("xl/workbook.xml"))
    order = [(s.get("name"), s.get(_NSR + "id")) for s in wb.iter(_NS + "sheet")]

    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {r.get("Id"): r.get("Target") for r in rels}

    sheets: list[Sheet] = []
    for name, rid in order:
        target = rid_to_target.get(rid)
        if not target:
            continue
        part = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
        try:
            root = ET.fromstring(z.read(part))
        except KeyError:
            continue
        sheet_data = root.find(_NS + "sheetData")
        if sheet_data is None:
            sheets.append(Sheet(name=name, headers=[]))
            continue

        raw_rows: list[dict[str, str]] = []
        for r in sheet_data.findall(_NS + "row"):
            cells: dict[str, str] = {}
            for c in r.findall(_NS + "c"):
                ref = c.get("r") or ""
                cells[_col_letters(ref)] = _cell_value(c, shared)
            raw_rows.append(cells)

        if not raw_rows:
            sheets.append(Sheet(name=name, headers=[]))
            continue

        header_cells = raw_rows[0]
        letters = sorted(header_cells.keys(), key=_col_key)
        headers = [header_cells[l].strip() for l in letters]
        letter_to_header = {l: header_cells[l].strip() for l in letters}

        rows: list[dict[str, str]] = []
        for cells in raw_rows[1:]:
            if not any((v or "").strip() for v in cells.values()):
                continue  # skip fully empty rows
            row = {h: "" for h in headers if h}
            for letter, val in cells.items():
                h = letter_to_header.get(letter)
                if h:
                    row[h] = val
            rows.append(row)
        sheets.append(Sheet(name=name, headers=headers, rows=rows))
    return sheets


def serial_to_iso(serial: float | int | str | None) -> str:
    if serial is None or serial == "":
        return ""
    try:
        days = int(float(serial))
    except (ValueError, TypeError):
        return ""
    try:
        return (_EXCEL_EPOCH + _dt.timedelta(days=days)).isoformat()
    except OverflowError:
        return ""


def write_csv(path: str, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: _fmt(row.get(c, "")) for c in columns})


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:.6g}"
    return str(value)


def write_xlsx(path: str, columns: list[str], rows: list[dict[str, Any]]) -> bool:
    """Write an XLSX mirror if openpyxl is available. Returns False if skipped."""
    try:
        from openpyxl import Workbook
    except ImportError:
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "ledger"
    ws.append(columns)
    for row in rows:
        ws.append([_xlsx_cell(row.get(c, "")) for c in columns])
    wb.save(path)
    return True


def _xlsx_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    return str(value)
