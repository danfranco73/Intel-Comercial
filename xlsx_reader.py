from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

DATE_FORMAT_TOKENS = ("yy", "dd", "mm", "m/", "d/", "h:", "ss")
BUILTIN_DATE_FORMATS = {
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    45,
    46,
    47,
}


def list_xlsx_files(base_dir):
    return sorted(
        item.name for item in Path(base_dir).iterdir() if item.suffix.lower() in {".xlsx", ".xlsm"}
    )


def read_sheet_names(path):
    workbook = _load_workbook(path)
    return [sheet["name"] for sheet in workbook["sheets"]]


def preview_sheet(path, sheet_name, preview_rows=12):
    workbook = _load_workbook(path)
    sheet_info = next((sheet for sheet in workbook["sheets"] if sheet["name"] == sheet_name), None)
    if not sheet_info:
        raise ValueError(f"No se encontró la hoja '{sheet_name}'")
    xml_data = workbook["archive"].read(sheet_info["target"])
    rows = _parse_sheet_rows(xml_data, workbook["shared_strings"], workbook["date_style_ids"], max_rows=preview_rows)
    header_row = detect_header_row(rows)
    headers = make_headers(rows[header_row] if rows else [])
    return {
        "name": sheet_name,
        "headerRow": header_row,
        "headers": headers,
        "preview": rows,
        "rowCount": infer_row_count(xml_data) or len(rows),
    }


def read_sheet_rows(workbook, sheet_name):
    sheet_info = next((sheet for sheet in workbook["sheets"] if sheet["name"] == sheet_name), None)
    if not sheet_info:
        raise ValueError(f"No se encontró la hoja '{sheet_name}'")
    xml_data = workbook["archive"].read(sheet_info["target"])
    return _parse_sheet_rows(xml_data, workbook["shared_strings"], workbook["date_style_ids"])


def _load_workbook(path):
    archive = zipfile.ZipFile(path)
    workbook_xml = ET.fromstring(archive.read("xl/workbook.xml"))
    workbook_rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    shared_strings = _read_shared_strings(archive)
    date_style_ids = _read_date_style_ids(archive)

    rel_map = {}
    for rel in workbook_rels.findall("pkgrel:Relationship", NS):
        rel_map[rel.attrib["Id"]] = "xl/" + rel.attrib["Target"]

    sheets = []
    for sheet in workbook_xml.findall("main:sheets/main:sheet", NS):
        sheets.append(
            {
                "name": sheet.attrib["name"],
                "target": rel_map[sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]],
            }
        )

    return {
        "archive": archive,
        "sheets": sheets,
        "shared_strings": shared_strings,
        "date_style_ids": date_style_ids,
    }


def _read_shared_strings(archive):
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    values = []
    for item in root.findall("main:si", NS):
        texts = []
        for node in item.iter():
            if node.tag == f"{{{NS['main']}}}t" and node.text:
                texts.append(node.text)
        values.append("".join(texts))
    return values


def _read_date_style_ids(archive):
    try:
        root = ET.fromstring(archive.read("xl/styles.xml"))
    except KeyError:
        return set()

    custom_num_fmts = {}
    numfmts = root.find("main:numFmts", NS)
    if numfmts is not None:
        for fmt in numfmts.findall("main:numFmt", NS):
            custom_num_fmts[int(fmt.attrib["numFmtId"])] = fmt.attrib.get("formatCode", "").lower()

    style_ids = set()
    cell_xfs = root.find("main:cellXfs", NS)
    if cell_xfs is None:
        return style_ids

    for idx, xf in enumerate(cell_xfs.findall("main:xf", NS)):
        num_fmt = int(xf.attrib.get("numFmtId", "0"))
        if num_fmt in BUILTIN_DATE_FORMATS:
            style_ids.add(idx)
            continue
        format_code = custom_num_fmts.get(num_fmt, "")
        if any(token in format_code for token in DATE_FORMAT_TOKENS) and "[h]" not in format_code:
            style_ids.add(idx)
    return style_ids


def _parse_sheet_rows(xml_data, shared_strings, date_style_ids, max_rows=None):
    rows = []
    max_col = 0
    row_tag = f"{{{NS['main']}}}row"
    cell_tag = f"{{{NS['main']}}}c"

    for _, row in ET.iterparse(BytesIO(xml_data), events=("end",)):
        if row.tag != row_tag:
            continue
        current = {}
        for cell in row.findall(cell_tag):
            ref = cell.attrib.get("r", "A1")
            col_idx = column_index(ref)
            current[col_idx] = read_cell_value(cell, shared_strings, date_style_ids)
            max_col = max(max_col, col_idx)

        ordered = []
        for idx in range(max_col + 1):
            ordered.append(current.get(idx, ""))
        if any(str(item).strip() for item in ordered):
            rows.append(ordered)
            if max_rows and len(rows) >= max_rows:
                break
        row.clear()
    return rows


def infer_row_count(xml_data):
    match = re.search(rb'<dimension[^>]*ref="[^"]*:(?:[A-Z]+)(\d+)"', xml_data[:2048])
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def read_cell_value(cell, shared_strings, date_style_ids):
    cell_type = cell.attrib.get("t")
    style_id = int(cell.attrib.get("s", "0"))
    value = cell.find("main:v", NS)

    if cell_type == "inlineStr":
        text_node = cell.find("main:is/main:t", NS)
        return text_node.text if text_node is not None else ""
    if value is None:
        return ""

    raw = value.text or ""
    if cell_type == "s":
        index = int(raw)
        return shared_strings[index] if index < len(shared_strings) else raw
    if cell_type == "b":
        return raw == "1"
    if cell_type == "str":
        return raw
    if style_id in date_style_ids and raw:
        return excel_date(raw)
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    return raw


def excel_date(raw_value):
    days = float(raw_value)
    base = datetime(1899, 12, 30)
    dt = base + timedelta(days=days)
    if dt.time().hour == 0 and dt.time().minute == 0 and dt.time().second == 0:
        return dt.date().isoformat()
    return dt.isoformat(sep=" ", timespec="minutes")


def column_index(ref):
    letters = "".join(char for char in ref if char.isalpha())
    idx = 0
    for char in letters:
        idx = idx * 26 + ord(char.upper()) - 64
    return max(idx - 1, 0)


def detect_header_row(rows):
    best_idx = 0
    best_score = -1
    for idx, row in enumerate(rows[:10]):
        strings = [str(cell).strip() for cell in row if str(cell).strip()]
        if not strings:
            continue
        unique_ratio = len(set(strings)) / max(len(strings), 1)
        alpha_ratio = sum(any(ch.isalpha() for ch in cell) for cell in strings) / len(strings)
        score = len(strings) * 0.7 + unique_ratio * 5 + alpha_ratio * 5
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx


def make_headers(row):
    headers = []
    for idx, value in enumerate(row):
        text = str(value).strip()
        headers.append(text or f"Columna {idx + 1}")
    return headers
