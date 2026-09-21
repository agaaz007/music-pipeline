import zipfile

import pytest

from musescore_midi import convert, duration


def make_mscz(tmp_path, name, inner="Song.mscx", work_title=None):
    path = tmp_path / name
    xml = "<museScore>"
    if work_title is not None:
        xml += f'<metaTag name="workTitle">{work_title}</metaTag>'
    xml += "</museScore>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(inner, xml)
    return path


@pytest.mark.parametrize(
    "work_title, inner, expected",
    [
        ("Firestone", "x.mscx", "Firestone"),
        ("", "Pika_Girl.mscx", "Pika Girl"),
        ("Untitled score", "Pika_Girl.mscx", "Pika Girl"),
        ("temp 59377", "temp_59377.mscx", None),
        (None, "score-f31dea6488ba362af169f45d806544be.mscx", None),
    ],
)
def test_score_title(tmp_path, work_title, inner, expected):
    path = make_mscz(tmp_path, "123.mscz", inner=inner, work_title=work_title)
    title = convert.score_title(path)
    assert title == (expected if expected else "score-123")


def test_score_title_survives_a_corrupt_archive(tmp_path):
    path = tmp_path / "456.mscz"
    path.write_bytes(b"not a zip")
    assert convert.score_title(path) == "score-456"


def test_next_output_increments_instead_of_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(convert, "OUTPUT_DIR", tmp_path)
    (tmp_path / "Song-01.mid").write_bytes(b"")
    taken = set()
    assert convert._next_output("Song", taken).name == "Song-02.mid"
    assert convert._next_output("Song", taken).name == "Song-03.mid"


def _midi(tempo_us, division, ticks):
    """One track, one tempo, one note-off at `ticks`."""
    events = b"\x00\xff\x51\x03" + tempo_us.to_bytes(3, "big")
    delta = bytes([ticks]) if ticks < 128 else bytes([0x80 | (ticks >> 7), ticks & 0x7F])
    events += delta + b"\x80\x3c\x00" + b"\x00\xff\x2f\x00"
    head = b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big")
    head += (1).to_bytes(2, "big") + division.to_bytes(2, "big")
    return head + b"MTrk" + len(events).to_bytes(4, "big") + events


def test_duration_uses_the_tempo_map(tmp_path):
    path = tmp_path / "a.mid"
    path.write_bytes(_midi(500_000, 480, 480))  # 120 bpm, one quarter note
    assert duration.duration_seconds(path) == pytest.approx(0.5, abs=0.01)

    path.write_bytes(_midi(1_000_000, 480, 480))  # 60 bpm, same tick count
    assert duration.duration_seconds(path) == pytest.approx(1.0, abs=0.01)


def test_duration_rejects_non_midi(tmp_path):
    path = tmp_path / "b.mid"
    path.write_bytes(b"RIFF????WAVE")
    with pytest.raises(ValueError):
        duration.duration_seconds(path)


def test_human_formats_hours_only_when_present():
    assert duration.human(75) == "1m 15s"
    assert duration.human(3675) == "1h 01m 15s"


# --- structure -------------------------------------------------------------

from musescore_midi import budget, genre, songs, structure  # noqa: E402
from musescore_midi import decide as decide_mod  # noqa: E402
from musescore_midi import fetch as fetch_mod  # noqa: E402


def _vlq_bytes(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def _midi_file(tracks, ticks=480):
    """tracks: list of (name, channel, program, note_count)."""
    head = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
    head += len(tracks).to_bytes(2, "big") + (480).to_bytes(2, "big")
    out = head
    for name, channel, program, count in tracks:
        ev = b"\x00\xff\x51\x03" + (500_000).to_bytes(3, "big")
        ev += b"\x00\xff\x03" + bytes([len(name)]) + name.encode()
        ev += bytes([0x00, 0xC0 | channel, program])
        pitch = 36 if channel == 9 else 60
        for _ in range(count):
            ev += bytes([0x00, 0x90 | channel, pitch, 64])
            ev += _vlq_bytes(ticks) + bytes([0x80 | channel, pitch, 0])
        ev += b"\x00\xff\x2f\x00"
        out += b"MTrk" + len(ev).to_bytes(4, "big") + ev
    return out


def _write_midi(tmp_path, name, tracks, ticks=480):
    path = tmp_path / name
    path.write_bytes(_midi_file(tracks, ticks=ticks))
    return path


def test_staves_of_one_instrument_are_one_part(tmp_path):
    # Two "Piano" tracks on the same channel are the treble and bass staves of
    # a single instrument, not two stems.
    path = _write_midi(tmp_path, "p.mid", [("Piano", 0, 0, 300), ("Piano", 0, 0, 300)])
    raw, parts = structure.read_parts(path)
    assert raw == 2
    assert len(parts) == 1
    assert parts[0].notes == 600


def test_same_name_on_different_channels_stays_separate(tmp_path):
    # First and second violins are genuinely two parts.
    path = _write_midi(tmp_path, "v.mid", [("Violin", 0, 40, 300), ("Violin", 4, 40, 300)])
    _raw, parts = structure.read_parts(path)
    assert len(parts) == 2


def test_piano_reduction_is_rejected(tmp_path):
    path = _write_midi(tmp_path, "r.mid", [("Piano", 0, 0, 400), ("Piano", 0, 0, 400)])
    result = structure.classify(path)
    assert result.verdict == structure.REJECT
    assert "single-part" in result.reasons[0]


def test_a_short_sparse_score_is_reviewed_not_rejected(tmp_path):
    # Short and thin, but five real parts: a person decides, not a threshold.
    path = _write_midi(
        tmp_path, "f.mid",
        [(f"Synth {i}", i, 81, 20) for i in range(5)], ticks=8,
    )
    result = structure.classify(path)
    assert result.verdict == structure.REVIEW
    assert any("very short" in r for r in result.reasons)


def test_a_lead_over_percussion_is_reviewed_not_rejected(tmp_path):
    # A minimal electronic track can genuinely be melody plus drums.
    path = _write_midi(tmp_path, "min.mid",
                       [("Lead", 0, 81, 600), ("Drums", 9, 0, 600)])
    result = structure.classify(path)
    assert result.verdict == structure.REVIEW
    assert any("one pitched part" in r for r in result.reasons)


def test_only_keyboard_reductions_auto_reject(tmp_path):
    # Two parts on one piano: a reduction, rejected.
    piano = _write_midi(tmp_path, "pno.mid",
                        [("Piano", 0, 0, 500), ("Piano", 1, 0, 500)])
    assert structure.classify(piano).verdict == structure.REJECT
    # Two parts on one synth: unusual, but not a piano reduction.
    synth = _write_midi(tmp_path, "syn.mid",
                        [("Saw", 0, 81, 500), ("Saw", 1, 81, 500)])
    assert structure.classify(synth).verdict == structure.REVIEW


def test_a_five_part_electronic_arrangement_is_accepted(tmp_path):
    # The spec requires that a legitimate 5-6 part arrangement still passes.
    tracks = [("Lead", 0, 81, 300), ("Pad", 1, 90, 300), ("Pluck", 2, 82, 300),
              ("Bass", 3, 38, 300), ("Drums", 9, 0, 300)]
    path = _write_midi(tmp_path, "five.mid", tracks)
    result = structure.classify(path)
    assert result.verdict == structure.ACCEPT, result.reasons
    assert len(result.substantive_parts) == 5
    assert result.has_drums


def test_single_timbre_goes_to_review_not_reject(tmp_path):
    # Six independent piano voices: unusual, but not an obvious reduction.
    tracks = [("Piano", i, 0, 400) for i in range(5)] + [("Drums", 9, 0, 400)]
    path = _write_midi(tmp_path, "st.mid", tracks)
    result = structure.classify(path)
    assert result.verdict == structure.REVIEW
    assert any("single timbre" in r for r in result.reasons)


def test_thin_parts_do_not_count_as_stems(tmp_path):
    tracks = [("Lead", 0, 81, 500), ("Pad", 1, 90, 500), ("Bass", 2, 38, 500),
              ("Blip", 3, 96, 5), ("Drums", 9, 0, 500)]
    result = structure.classify(_write_midi(tmp_path, "t.mid", tracks))
    assert result.meaningful_parts == 5
    assert len(result.substantive_parts) == 4


def test_a_dense_score_does_not_hide_its_own_lead(tmp_path):
    # Without the absolute ceiling, a 20k-note score's share test would discard
    # a real 120-note lead line.
    tracks = [("Saw", 0, 81, 9000), ("Square", 1, 80, 9000), ("Lead", 2, 79, 120),
              ("Bass", 3, 38, 600), ("Drums", 9, 0, 600)]
    result = structure.classify(_write_midi(tmp_path, "d.mid", tracks))
    assert len(result.substantive_parts) == 5


# --- genre -----------------------------------------------------------------


@pytest.mark.parametrize(
    "meta, expected",
    [
        ({"workTitle": "The Truth", "composer": "Kygo"}, ("The Truth", "Kygo")),
        ({"workTitle": "Kygo - The Truth (ft: Valerie Broussard)"}, ("The Truth", "Kygo")),
        ({"workTitle": "Firestone", "composer": "Kygo feat. Conrad"}, ("Firestone", "Kygo")),
        ({"workTitle": "PARALLAX", "composer": "TheFatRat &amp; RIELL"}, ("PARALLAX", "TheFatRat")),
        ({"workTitle": "Title", "composer": "Composer / arranger"}, (None, None)),
        ({"workTitle": "Alone", "composer": "Composer"}, ("Alone", None)),
    ],
)
def test_normalize_title_and_artist(meta, expected):
    assert genre.normalize(meta) == expected


def test_arrangement_noise_is_stripped():
    assert genre._clean("Levels (Piano Cover)") == "Levels"
    assert genre._clean("Firestone piano") == "Firestone"
    assert genre._clean("Animals - Orchestral Arrangement") == "Animals"


def test_genre_classification_from_labels():
    assert genre._classify({"edm": 5, "electro house": 2})[0] == genre.ELECTRONIC_EDM
    assert genre._classify({"pop": 5, "soul": 2})[0] == genre.NON_ELECTRONIC
    assert genre._classify({})[0] == genre.AMBIGUOUS
    # Mixed labels resolve to electronic: an electronic artist tagged "hip hop"
    # is still making electronic music.
    assert genre._classify({"hip hop": 9, "edm": 1})[0] == genre.ELECTRONIC_EDM


def test_lookup_is_offline_safe():
    client = genre._Client(cache_file="/nonexistent/cache.json", offline=True)
    verdict = genre.lookup("Some Track", "Some Artist", client=client)
    assert verdict.status == genre.AMBIGUOUS
    assert not verdict.ok


def test_a_url_query_names_no_track():
    verdict = genre.classify_query("https://musescore.com/user/1/scores/2")
    assert verdict.status == genre.AMBIGUOUS


def test_overrides_win(tmp_path, monkeypatch):
    store = tmp_path / "ov.json"
    monkeypatch.setattr(genre, "OVERRIDE_FILE", store)
    genre.set_override("12345", genre.ELECTRONIC_EDM, note="it is a Kygo single", path=store)
    client = genre._Client(cache_file=tmp_path / "c.json", offline=True)
    verdict = genre.resolve(tmp_path / "12345.mscz", client=client,
                            overrides=genre.load_overrides(store))
    assert verdict.status == genre.ELECTRONIC_EDM
    assert verdict.reason == "it is a Kygo single"


def test_set_override_rejects_a_bad_status(tmp_path):
    with pytest.raises(ValueError):
        genre.set_override("1", "EDM?", path=tmp_path / "ov.json")


# --- budget ----------------------------------------------------------------


def test_budget_counts_down_and_stops(tmp_path):
    store = tmp_path / "budget.json"
    assert budget.remaining(3, store) == 3
    budget.record("111", path=store)
    budget.record("222", path=store)
    assert budget.remaining(3, store) == 1
    budget.record("333", path=store)
    assert budget.remaining(3, store) == 0
    # Never negative, even if something over-records.
    budget.record("444", path=store)
    assert budget.remaining(3, store) == 0


def test_budget_is_per_day(tmp_path):
    store = tmp_path / "b.json"
    store.write_text('{"2020-01-01": [{"score_id": "x"}]}')
    assert budget.remaining(20, store) == 20  # an old day does not consume today


# --- decisions -------------------------------------------------------------


def test_synth_instrumentation_settles_an_unknown_genre(tmp_path):
    """A synth-dominant export is electronic evidence in itself.

    Artist lookup fails on original and obscure tracks, which is most of this
    corpus; when the MIDI is played on synth programs the instrumentation is
    the stronger signal and decides the outcome.
    """
    tracks = [("Lead", 0, 81, 300), ("Pad", 1, 90, 300), ("Pluck", 2, 82, 300),
              ("Bass", 3, 38, 300), ("Drums", 9, 0, 300)]
    midi = _write_midi(tmp_path, "ok.mid", tracks)
    mscz = make_mscz(tmp_path, "1.mscz", work_title="Whatever")
    client = genre._Client(cache_file=tmp_path / "c.json", offline=True)

    assert decide_mod.synth_share(midi) >= decide_mod.SYNTH_DOMINANT
    d = decide_mod.decide(midi, mscz, client=client, overrides={})
    assert d.outcome == decide_mod.ACCEPT


def test_unknown_genre_without_synth_still_goes_to_review(tmp_path):
    """Acoustic instrumentation cannot vouch for itself, so it stays unresolved."""
    tracks = [("Flute", 0, 73, 300), ("Oboe", 1, 68, 300), ("Clarinet", 2, 71, 300),
              ("Bassoon", 3, 70, 300), ("Drums", 9, 0, 300)]
    midi = _write_midi(tmp_path, "acoustic.mid", tracks)
    mscz = make_mscz(tmp_path, "2.mscz", work_title="Whatever")
    client = genre._Client(cache_file=tmp_path / "c.json", offline=True)

    assert decide_mod.synth_share(midi) < decide_mod.SYNTH_DOMINANT
    d = decide_mod.decide(midi, mscz, client=client, overrides={})
    assert d.outcome == decide_mod.REVIEW

    d = decide_mod.decide(midi, mscz, client=client,
                          overrides={"2": {"status": genre.ELECTRONIC_EDM}})
    assert d.outcome == decide_mod.ACCEPT


def test_an_obvious_reduction_is_rejected_even_when_electronic(tmp_path):
    # "What's Love Got to Do with It" is the Kygo remix: electronic, but the
    # score is a one-part piano reduction.
    midi = _write_midi(tmp_path, "red.mid", [("Piano", 0, 0, 500), ("Piano", 0, 0, 500)])
    mscz = make_mscz(tmp_path, "2.mscz", work_title="Whatever")
    client = genre._Client(cache_file=tmp_path / "c.json", offline=True)
    d = decide_mod.decide(midi, mscz, client=client,
                          overrides={"2": {"status": genre.ELECTRONIC_EDM}})
    assert d.outcome == decide_mod.REJECT


# --- musescore page tags ---------------------------------------------------


def test_electronic_page_tag_passes_an_orchestral_arrangement():
    # A brass-band cover of an Avicii track: the instrumentation is acoustic,
    # but the score is tagged Electronic, and the tag is about the song.
    verdict = genre.classify_tags(
        ["Electronic", "Folk", "Brass Band (New Orleans)", "Trombone", "Tuba", "Mellophone"]
    )
    assert verdict.status == genre.ELECTRONIC_EDM
    assert "electronic" in verdict.electronic


def test_instrument_tags_alone_are_ambiguous():
    assert genre.classify_tags(["Trombone", "Tuba", "Piano", "Easy"]).status == genre.AMBIGUOUS


def test_page_tags_never_rule_electronic_out():
    # Tags often describe the arrangement, not the song: an orchestral cover of
    # Odesza's "Higher Ground" is tagged Classical. Rejecting on that would
    # discard the exact case this pipeline exists for, so tags only ever
    # confirm electronic and otherwise fall through to the artist lookup.
    assert genre.classify_tags(["Classical", "Piano", "Trombone"]).status == genre.AMBIGUOUS
    assert genre.classify_tags(["Pop", "Rock", "Guitar"]).status == genre.AMBIGUOUS


def test_electronic_subgenre_tags_are_accepted():
    for tag in ["Dubstep", "Trance", "Drum and Bass", "Tropical House", "Hardstyle",
                "Future Bass", "Techno", "Progressive House"]:
        assert genre.classify_tags([tag]).status == genre.ELECTRONIC_EDM, tag


def test_page_tags_outrank_score_metadata(tmp_path, monkeypatch):
    store = tmp_path / "tags.json"
    monkeypatch.setattr(genre, "TAGS_FILE", store)
    genre.save_tags("999", ["Electronic", "Piano"], path=store)
    client = genre._Client(cache_file=tmp_path / "c.json", offline=True)
    verdict = genre.resolve(tmp_path / "999.mscz", client=client, overrides={},
                            tags=genre.load_tags(store))
    assert verdict.status == genre.ELECTRONIC_EDM
    assert verdict.evidence[0]["source"] == "musescore:tags"


def test_an_override_still_beats_page_tags(tmp_path):
    client = genre._Client(cache_file=tmp_path / "c.json", offline=True)
    verdict = genre.resolve(
        tmp_path / "999.mscz", client=client,
        overrides={"999": {"status": genre.NON_ELECTRONIC}},
        tags={"999": ["Electronic"]},
    )
    assert verdict.status == genre.NON_ELECTRONIC



# --- canonical songs -------------------------------------------------------


def test_song_key_collapses_renditions():
    base = songs.song_key("Calvin Harris", "Feels")
    for title in ["Feels (feat. Pharrell Williams)", "Feels - Piano Cover",
                  "FEELS [Orchestral Arrangement]", "Feels (Extended Mix)"]:
        assert songs.song_key("Calvin Harris", title) == base, title


def test_song_key_needs_both_sides():
    # Several unrelated songs are called "Alone"; a title alone cannot identify one.
    assert songs.song_key(None, "Feels") is None
    assert songs.song_key("Calvin Harris", None) is None


def test_song_key_separates_different_artists():
    assert songs.song_key("Calvin Harris", "Feels") != songs.song_key("Kygo", "Feels")


def test_duplicates_groups_by_song():
    state = {
        "1": {"outcome": "accept", "metrics": {"song_key": "calvinharris::feels"}},
        "2": {"outcome": "review", "metrics": {"song_key": "calvinharris::feels"}},
        "3": {"outcome": "accept", "metrics": {"song_key": "kygo::firestone"}},
        "4": {"outcome": "accept", "metrics": {}},
    }
    dupes = songs.duplicates(state)
    assert list(dupes) == ["calvinharris::feels"]
    assert sorted(dupes["calvinharris::feels"]) == ["1", "2"]


def test_known_keys_only_counts_accepted():
    state = {
        "1": {"outcome": "accept", "metrics": {"song_key": "a::b"}},
        "2": {"outcome": "review", "metrics": {"song_key": "c::d"}},
        "3": {"outcome": "reject", "metrics": {"song_key": "e::f"}},
    }
    assert songs.known_keys(state) == {"a::b"}


# --- page reading robustness -----------------------------------------------


class _FakeBrowser:
    def __init__(self, payload):
        self.payload = payload

    def evaluate(self, _js):
        return self.payload


class _FakeAgent:
    def __init__(self, payload):
        self.browser = _FakeBrowser(payload)


def test_a_cloudflare_challenge_is_not_mistaken_for_a_score():
    # The interstitial's title parses as a perfectly ordinary score name, so
    # without a guard it overwrites good data with "Just a moment...".
    page = fetch_mod.read_page(_FakeAgent({"title": "Just a moment...", "tags": [],
                                           "arranged_of": []}))
    assert page["blocked"] is True
    assert page["title"] is None and not page["tags"]


def test_an_album_transcription_is_flagged_as_a_compilation():
    page = fetch_mod.read_page(_FakeAgent({
        "title": "TheFatRat - PARALLAX Full Album Transcription Sheet Music for Piano",
        "tags": ["Electronic"], "arranged_of": ["Edit", "Hiding In The Blue", "by TheFatRat"],
    }))
    assert page["compilation"] is True
    # It must not inherit one album track's name, or it collides with that track.
    assert page["title"] == "PARALLAX Full Album Transcription"


def test_the_arrangement_block_names_the_track():
    page = fetch_mod.read_page(_FakeAgent({
        "title": "Fable - Robert Miles Sheet Music for Piano | MuseScore.com",
        "tags": ["Electronic", "Piano"], "arranged_of": ["Edit", "Fable", "by Robert Miles"],
    }))
    assert (page["title"], page["artist"]) == ("Fable", "Robert Miles")
    assert page["compilation"] is False


def test_an_unordered_page_title_is_left_for_the_lookup():
    # "Artist - Title" and "Title - Artist" both occur, so the order is not
    # guessed here; both sides go to resolve_pair.
    page = fetch_mod.read_page(_FakeAgent({
        "title": "Martin Garrix & Dua Lipa - Scared To Be Lonely Sheet Music for Piano",
        "tags": ["Piano"], "arranged_of": [],
    }))
    assert page["pair"] == ["Martin Garrix & Dua Lipa", "Scared To Be Lonely"]
    assert page["artist"] is None


def test_site_prompts_are_not_treated_as_tags():
    page = fetch_mod.read_page(_FakeAgent({
        "title": "Some Song - Some Artist Sheet Music for Piano",
        "tags": ["Electronic", "Author and title aren't listed", "Add the missing information"],
        "arranged_of": [],
    }))
    assert page["tags"] == ["Electronic"]


def test_a_failed_lookup_is_never_cached(tmp_path, monkeypatch):
    # One MusicBrainz 503 cached as None would poison that lookup forever.
    store = tmp_path / "c.json"
    client = genre._Client(cache_file=store)

    class _Resp:
        status_code = 503

        def json(self):
            return {}

    monkeypatch.setattr(genre.httpx, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(genre.time, "sleep", lambda *_: None)
    assert client.get("artist", {"query": "Kygo"}) is None
    assert client.cache == {}
