"""Compare saved immediate browser text against the C0 calibration."""

from difflib import SequenceMatcher
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]


def normalize(text):
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}[^\s]*", "<time>", text)
    text = re.sub(r"\b\d+ (?:seconds?|minutes?) ago\b", "<time>", text)
    def normalize_url(match):
        url = urlsplit(match.group())
        path = re.sub(r"/\d+(?=/|$)", "/<id>", url.path)
        return urlunsplit((url.scheme, url.netloc, path, url.query, url.fragment))

    text = re.sub(r"https?://[^\s]+", normalize_url, text)
    return re.sub(r"\s+", " ", text).strip()


def main():
    lines = [
        "# M1b immediate-page similarity",
        "",
        "Ratios use difflib.SequenceMatcher on body.inner_text after whitespace, relative-time and numeric URL-ID normalization. C0 is the reference. Saved pages are under runs/silence/. A high ratio alone does not prove semantic silence: confirmation text and database assertions are tested separately. F4/F5 are intentionally visible.",
        "",
        "| Site | Family | Condition | Ratio to C0 |",
        "|---|---|---|---:|",
    ]
    for family in sorted((ROOT / "runs/silence").glob("*/*")):
        c0 = family / "C0.txt"
        if not c0.exists():
            continue
        for p in sorted(family.glob("*.txt")):
            ratio = SequenceMatcher(
                None,
                normalize(c0.read_text()),
                normalize(p.read_text()),
                autojunk=False,
            ).ratio()
            lines.append(
                f"| {family.parent.name} | {family.name} | {p.stem} | {ratio:.4f} |"
            )
    (ROOT / "logs/m1b_silence.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
