"""Paths and constants for the MuseScore -> MIDI pipeline."""

import os
from pathlib import Path

HOME = Path.home()

# MuseScore Studio drops every score opened via the website's "Edit" button here,
# named <score_id>.mscz. This is the handoff point between stage 1 and stage 2.
CLOUD_SCORES = Path(
    os.environ.get(
        "MUSESCORE_CLOUD_SCORES",
        HOME / "Library/Application Support/MuseScore/MuseScore4/cloud_scores",
    )
)

# Where finished .mid files land.
OUTPUT_DIR = Path(os.environ.get("MIDI_OUTPUT_DIR", HOME / "Documents/MuseScore4/Scores"))

# MuseScore's CLI. Same binary as the app; -o and -j need no GUI session.
MSCORE = Path(os.environ.get("MSCORE_BIN", "/Applications/MuseScore 4.app/Contents/MacOS/mscore"))

# Tracks what we already converted, so reruns are incremental.
STATE_FILE = Path(os.environ.get("PIPELINE_STATE", HOME / ".musescore_midi_state.json"))

MUSESCORE_SEARCH = "https://musescore.com/sheetmusic?text={query}"

# How long to wait for MuseScore Studio to finish writing a .mscz after the
# browser triggers the musescore:// handoff.
HANDOFF_TIMEOUT_S = float(os.environ.get("HANDOFF_TIMEOUT_S", "180"))
