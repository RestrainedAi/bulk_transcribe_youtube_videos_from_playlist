import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aeonimus_handoff as ah  # noqa: E402
import youtube_intake as yi  # noqa: E402

SID = "1f859585-68cb-4440-b38e-3871c7088658"


@pytest.mark.parametrize(
    "url",
    [
        f"https://suno.com/song/{SID}",
        f"suno.com/song/{SID}?sh=abc123",
        f"https://www.suno.com/song/{SID.upper()}",
        f"https://app.suno.ai/song/{SID}/",
        f"https://suno.com/embed/{SID}",
        f"https://cdn1.suno.ai/{SID}.mp3",
    ],
)
def test_suno_song_links(url):
    link = yi.parse_youtube_url(url)
    assert link.kind == "suno" and link.id == SID
    assert link.key == f"suno:{SID}"
    assert link.file_tag == f"suno-{SID}"
    assert link.url == f"https://suno.com/song/{SID}"


@pytest.mark.parametrize(
    "url,needle",
    [
        ("https://suno.com/s/AbCdEf", "short Suno share link"),
        ("https://suno.com/@someone", "isn't a single song"),
        ("https://suno.com/song/not-a-uuid", "broken song ID"),
        ("https://suno.com/", "couldn't find a song"),
    ],
)
def test_bad_suno_links(url, needle):
    with pytest.raises(yi.LinkError, match=needle):
        yi.parse_youtube_url(url)


def test_suno_and_youtube_mixed_in_csv(tmp_path):
    f = tmp_path / "links.csv"
    f.write_text(
        "title,url\n"
        f"a,https://suno.com/song/{SID}\n"
        f"b,https://suno.com/song/{SID}?sh=x\n"
        "c,https://www.youtube.com/watch?v=jNQXAC9IVRw\n",
        encoding="utf-8",
    )
    r = yi.read_links_file(f)
    assert [l.kind for l in r.links] == ["suno", "video"]
    assert r.duplicates == 1


PAGE = (
    '<meta property="og:title" content="My &amp; Song"/>'
    '<script>self.__next_f.push([1,"{\\"id\\":\\"' + SID + '\\",\\"is_public\\":true,'
    '\\"video_url\\":\\"https://cdn1.suno.ai/' + SID + '.mp4\\",'
    '\\"audio_url\\":\\"https://studio-api.prod.suno.com/api/forbidden\\",'
    '\\"media_urls\\":[{\\"url\\":\\"https://d2lwuy8qc234o3.cloudfront.net/1/clip/' + SID + '.m4a\\"}]}"])</script>'
)


def test_page_parsing_prefers_streamed_audio_then_video():
    title, urls = yi.suno_media_from_page(PAGE, SID)
    assert title == "My & Song"
    assert urls == [
        f"https://d2lwuy8qc234o3.cloudfront.net/1/clip/{SID}.m4a",
        f"https://cdn1.suno.ai/{SID}.mp4",
    ]


def test_private_song_is_a_plain_message():
    page = PAGE.replace('is_public\\":true', 'is_public\\":false')
    with pytest.raises(yi.SunoError, match="private"):
        yi.suno_media_from_page(page, SID)


def test_page_without_audio_gives_no_urls():
    assert yi.suno_media_from_page('<meta property="og:title" content="x"/>', SID)[1] == []


def test_friendly_error_passes_suno_message_through():
    assert yi.friendly_download_error(yi.SunoError("This Suno song wasn't found.")) == "This Suno song wasn't found."


def test_safe_title():
    assert yi._safe_title('a<b>:"c/d\\e|f?g* ok', "x") == "abcdefg ok"
    assert yi._safe_title("  ", "Suno song") == "Suno song"
    assert len(yi._safe_title("é" * 200, "x").encode()) <= 90


def test_existing_suno_file_is_found(tmp_path):
    (tmp_path / f"Song [suno-{SID}].m4a").write_bytes(b"x")
    assert yi.find_existing(f"suno-{SID}", [tmp_path]) is not None


def test_suno_transcripts_are_shared_with_their_key(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv(ah.CONFIG_ENV, str(tmp_path / "cfg" / "handoff.json"))
    cfg = ah.load_config()
    txt = tmp_path / f"My Song [suno-{SID}]_1.txt"
    js = tmp_path / f"My Song [suno-{SID}]_1.json"
    txt.write_text("la la", encoding="utf-8")
    js.write_text("{}", encoding="utf-8")
    names = sorted(p.name for p in ah.publish_transcript(txt, js, cfg))
    assert names == [f"My Song [suno-{SID}].json", f"My Song [suno-{SID}].txt"]
