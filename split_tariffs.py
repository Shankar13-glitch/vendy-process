"""
split_tariffs.py
----------------
Splits 'Consolidated JSONS for all ports.txt' into individual
tariffs/<port>.json files, one per port.

Strategy: section headers are the authoritative boundaries.
  1. Scan for header lines → split file into section blobs
  2. Within each blob, find the first '{' and use raw_decode() to extract JSON
  3. Write to tariffs/<filename>.json

Two header formats exist in the source file:
  Format A (European):  PortName/Boluda   (or / boluda with spaces)
  Format B (Mexican):   PortName          (no /Boluda suffix)

Special cases:
  - Tenerife/La Palma: no section header; detected via port_metadata.port_name
  - Ceuta: two header lines before JSON ("Cueta" then "cueta Spain / Boluda")
  - Algeciras: already exists in tariffs/ — skipped
  - Santo_Domingo_Haina: JSON missing final '}' — auto-repair applied
"""

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# HEADER → (profile_key, output_filename) mapping
# Keys are normalised: lowercased, /operator suffix stripped, whitespace stripped.
# Value None = skip (already exists).
# ---------------------------------------------------------------------------

HEADER_MAP = {
    "ghent":                        ("Ghent",               "ghent.json"),
    "lehavre":                      ("Le_Havre",            "le_havre.json"),
    "antwerp":                      ("Antwerp",             "antwerp.json"),
    "panama":                       ("Panama",              "panama.json"),
    "rotterdam":                    ("Rotterdam",           "rotterdam.json"),
    "dordrecht and moerdijk":       ("Dordrecht_Moerdijk",  "dordrecht_moerdijk.json"),
    "santo domingo and haina":      ("Santo_Domingo_Haina", "santo_domingo_haina.json"),
    "brake":                        ("Brake",               "brake.json"),
    "rostock":                      ("Rostock",             "rostock.json"),
    "altamira":                     ("Altamira",            "altamira.json"),
    "coatzacolcos":                 ("Coatzacoalcos",       "coatzacoalcos.json"),
    "ensenada port":                ("Ensenada",            "ensenada.json"),
    "guaymas":                      ("Guaymas",             "guaymas.json"),
    "manzanillo":                   ("Manzanillo",          "manzanillo.json"),
    "mazatl\u00e1n":                ("Mazatlan",            "mazatlan.json"),
    "salina cruz":                  ("Salina_Cruz",         "salina_cruz.json"),
    "tampico":                      ("Tampico",             "tampico.json"),
    "castello port - spain":        ("Castellon",           "castellon.json"),
    "huelva":                       ("Huelva",              "huelva.json"),
    "cueta":                        ("Ceuta",               "ceuta.json"),
    "cueta spain":                  ("Ceuta",               "ceuta.json"),
    "caiz bay":                     ("Cadiz_Bay",           "cadiz_bay.json"),
    "valencia":                     ("Valencia",            "valencia.json"),
    "las palmas":                   ("Las_Palmas",          "las_palmas.json"),
    "algeciras":                    None,   # already exists — skip
}

# Noise header lines that look like section headers but aren't ports
NOISE_HEADERS = {
    "updated antifer weather rebate logic",
    "revised json shifting logic for rotterdam",
    "consolidated jsons for all ports",
}

# Secondary lookup: port_metadata.port_name → mapping (for headerless sections)
PORT_NAME_MAP = {
    "santa cruz de tenerife and santa cruz de la palma":
        ("Tenerife_La_Palma", "tenerife_la_palma.json"),
}


def _normalise_header(raw: str) -> str:
    """Strip /operator suffix, lowercase, collapse whitespace."""
    if "/" in raw:
        raw = raw[:raw.rfind("/")]
    return re.sub(r"\s+", " ", raw.strip().lower())


def _is_port_header(line: str):
    """
    Returns (profile_key, filename) if line is a recognised port header,
    None if it's a skip (Algeciras),
    False if it's not a header at all.
    """
    stripped = line.strip()
    if not stripped or stripped[0] in '{[}]"#0123456789':
        return False
    # Reject lines that look like JSON or prose (contain : or are too long)
    if len(stripped) > 80:
        return False
    norm = _normalise_header(stripped)
    if norm in NOISE_HEADERS:
        return False
    if norm in HEADER_MAP:
        return HEADER_MAP[norm]
    return False


def _extract_json(blob: str):
    """
    Extract the first complete JSON object from blob text.
    Uses raw_decode so extra trailing text is ignored.
    If the JSON is missing the final '}', appends one and retries.
    Returns (parsed_dict, end_pos, note) where:
      - end_pos is the position in blob right after the extracted JSON
      - note is '' or 'auto-repaired'
    Raises ValueError if parsing still fails.
    """
    idx = blob.find("{")
    if idx == -1:
        raise ValueError("No '{' found in section blob")

    decoder = json.JSONDecoder()

    # First attempt: parse as-is
    try:
        obj, end = decoder.raw_decode(blob, idx)
        return obj, end, ""
    except json.JSONDecodeError:
        pass

    # Second attempt: append one closing '}' (handles missing final brace)
    try:
        obj, end = decoder.raw_decode(blob[idx:] + "\n}", 0)
        return obj, idx + end, "auto-repaired (appended '}')"
    except json.JSONDecodeError:
        pass

    # Third attempt: use brace depth to find natural end
    text = blob[idx:]
    depth = 0
    end = len(text)
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        obj = json.loads(text[:end])
        return obj, idx + end, ""
    except json.JSONDecodeError as exc:
        try:
            obj = json.loads(text[:end] + "}")
            return obj, idx + end, "auto-repaired (forced close)"
        except json.JSONDecodeError:
            raise ValueError(f"JSON parse failed: {exc}") from exc


def split(source_path: Path, tariffs_dir: Path):
    tariffs_dir.mkdir(exist_ok=True)

    # ----------------------------------------------------------------
    # Phase 1: split file into (header_info, blob_text) sections
    # ----------------------------------------------------------------
    with source_path.open(encoding="utf-8", errors="replace") as fh:
        all_lines = fh.readlines()

    sections = []          # list of (header_result, [lines])
    current_header = None  # header_result (can be False for orphan sections)
    current_lines = []

    for line in all_lines:
        h = _is_port_header(line)
        if h is not False:
            # New section starts
            if current_lines:
                sections.append((current_header, current_lines))
            current_header = h
            current_lines = []
        else:
            current_lines.append(line)

    # Flush last section
    if current_lines:
        sections.append((current_header, current_lines))

    # ----------------------------------------------------------------
    # Phase 2: process each section
    # ----------------------------------------------------------------
    results = []

    for header_result, blob_lines in sections:
        blob = "".join(blob_lines)

        # Skip sections with no JSON content
        if "{" not in blob:
            continue

        if header_result is None:
            # Explicitly skipped (Algeciras)
            continue

        elif header_result is False:
            # Orphan section (no recognised header) — may be Tenerife or noise.
            # Try to identify via port_metadata.port_name.
            remaining = blob
            while "{" in remaining:
                try:
                    parsed, end, note = _extract_json(remaining)
                except (ValueError, Exception):
                    break
                port_name_raw = (
                    parsed.get("port_metadata", {}).get("port_name", "")
                    or parsed.get("port_name", "")
                )
                lookup_key = port_name_raw.strip().lower()
                mapping = PORT_NAME_MAP.get(lookup_key)
                if mapping:
                    profile_key, filename = mapping
                    out_path = tariffs_dir / filename
                    out_path.write_text(
                        json.dumps(parsed, indent=2, ensure_ascii=False),
                        encoding="utf-8"
                    )
                    size = out_path.stat().st_size
                    top_keys = len(parsed) if isinstance(parsed, dict) else "?"
                    note_str = note if note else f"{top_keys} top-level keys"
                    results.append((profile_key, filename, f"{size:,} bytes", note_str))
                remaining = remaining[end:]

        else:
            # Named section — extract first JSON (ignore supplementary patch blobs)
            profile_key, filename = header_result
            try:
                parsed, end, note = _extract_json(blob)
            except ValueError as exc:
                print(f"  [WARN] Parse error for '{profile_key}': {exc}")
                results.append((profile_key, filename or "?", "PARSE ERROR", str(exc)[:60]))
                continue

            if filename is None:
                continue

            out_path = tariffs_dir / filename
            out_path.write_text(
                json.dumps(parsed, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            size = out_path.stat().st_size
            top_keys = len(parsed) if isinstance(parsed, dict) else "?"
            note_str = note if note else f"{top_keys} top-level keys"
            results.append((profile_key, filename, f"{size:,} bytes", note_str))

            # Check if there are additional unnamed JSON objects in this section
            # (e.g. Tenerife follows Castellon with no header)
            remaining = blob[end:]
            while "{" in remaining:
                try:
                    extra, end2, enote = _extract_json(remaining)
                except (ValueError, Exception):
                    break
                port_name_raw = (
                    extra.get("port_metadata", {}).get("port_name", "")
                    or extra.get("port_name", "")
                )
                lookup_key = port_name_raw.strip().lower()
                mapping = PORT_NAME_MAP.get(lookup_key)
                if mapping:
                    pk, fn = mapping
                    out_path2 = tariffs_dir / fn
                    out_path2.write_text(
                        json.dumps(extra, indent=2, ensure_ascii=False),
                        encoding="utf-8"
                    )
                    size2 = out_path2.stat().st_size
                    top2 = len(extra) if isinstance(extra, dict) else "?"
                    note2 = enote if enote else f"{top2} top-level keys"
                    results.append((pk, fn, f"{size2:,} bytes", note2))
                remaining = remaining[end2:]

    # ----------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------
    print(f"\n{'Port Key':<28} {'File':<35} {'Size':<15} {'Notes'}")
    print("-" * 95)
    for profile_key, filename, size, note in results:
        print(f"{profile_key:<28} {filename:<35} {size:<15} {note}")

    ok = len([r for r in results if "bytes" in r[2]])
    errors = len([r for r in results if "ERROR" in r[2]])
    print(f"\n{ok} files written  |  {errors} errors  ->  {tariffs_dir}/")


if __name__ == "__main__":
    project_root = Path(__file__).parent
    source = project_root / "Consolidated JSONS for all ports.txt"
    tariffs = project_root / "tariffs"

    if not source.exists():
        print(f"ERROR: source file not found: {source}")
        sys.exit(1)

    print(f"Splitting: {source.name}")
    print(f"Output:    {tariffs}/\n")
    split(source, tariffs)
