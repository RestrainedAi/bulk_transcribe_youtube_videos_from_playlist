import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import youtube_intake as yi  # noqa: E402

VID = "dQw4w9WgXcQ"
PL = "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"youtube.com/watch?v={VID}&t=42s",
        f"https://m.youtube.com/watch?feature=share&v={VID}",
        f"https://music.youtube.com/watch?v={VID}",
        f"https://youtu.be/{VID}?si=abc",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/live/{VID}?feature=share",
        f"https://www.youtube-nocookie.com/embed/{VID}",
        f"https://www.youtube.com/watch?v={VID}&list={PL}&index=3",
        f"  <https://www.youtube.com/watch?v={VID}>  ",
    ],
)
def test_video_links(url):
    link = yi.parse_youtube_url(url)
    assert link.kind == "video" and link.id == VID
    assert link.url == f"https://www.youtube.com/watch?v={VID}"


def test_playlist_link():
    link = yi.parse_youtube_url(f"https://www.youtube.com/playlist?list={PL}")
    assert link.kind == "playlist" and link.id == PL


@pytest.mark.parametrize(
    "url, needle",
    [
        ("", "Paste"),
        ("https://vimeo.com/12345", "isn't a YouTube"),
        ("https://www.youtube.com/@SomeChannel", "channel link"),
        ("https://www.youtube.com/@SomeChannel/videos", "channel link"),
        ("https://www.youtube.com/channel/UC1234567890", "channel link"),
        ("https://www.youtube.com/post/UgkxABC", "community post"),
        ("https://www.youtube.com/watch?v=short", "broken video ID"),
        ("https://www.youtube.com/playlist?list=RDdQw4w9WgXcQ", "Mix"),
        ("https://www.youtube.com/playlist?list=WL", "private list"),
        ("https://www.youtube.com/", "couldn't find"),
    ],
)
def test_bad_links_have_plain_reasons(url, needle):
    with pytest.raises(yi.LinkError) as exc:
        yi.parse_youtube_url(url)
    assert needle in str(exc.value)


def test_extract_dedupes_across_forms():
    text = f"https://youtu.be/{VID}\nhttps://www.youtube.com/watch?v={VID}\nhttps://www.youtube.com/shorts/abcdefghijk"
    r = yi.extract_links(text)
    assert [link.id for link in r.links] == [VID, "abcdefghijk"]
    assert r.duplicates == 1


def test_csv_any_column_url_lister_shape(tmp_path):
    csv_text = (
        "channel,type,title,url,channel_url\n"
        f'My Channel,video,"Hello, world",https://www.youtube.com/watch?v={VID},https://www.youtube.com/@MyChannel\n'
        'My Channel,short,Tiny,https://www.youtube.com/shorts/abcdefghijk,https://www.youtube.com/@MyChannel\n'
        'My Channel,post,Note,https://www.youtube.com/post/UgkxABCDEF,https://www.youtube.com/@MyChannel\n'
        f'My Channel,playlist,List,https://www.youtube.com/playlist?list={PL},https://www.youtube.com/@MyChannel\n'
        f'My Channel,video,Again,"https://www.youtube.com/watch?v={VID}&t=5",\n'
    )
    p = tmp_path / "My Channel_all_2026-10-17.csv"
    p.write_text(csv_text, encoding="utf-8-sig")
    r = yi.read_links_file(p)
    assert [(link.kind, link.id) for link in r.links] == [("video", VID), ("video", "abcdefghijk"), ("playlist", PL)]
    assert r.duplicates == 1
    # 4 channel links + 1 post are skipped, not errors
    assert len(r.skipped) == 5
    assert "skipped" in yi.describe_intake(r, 3, 0)


def test_txt_and_utf16(tmp_path):
    p = tmp_path / "links.txt"
    p.write_text(f"watch this: youtu.be/{VID}.\n", encoding="utf-16")
    r = yi.read_links_file(p)
    assert [link.id for link in r.links] == [VID]


def test_missing_file():
    with pytest.raises(yi.LinkError):
        yi.read_links_file("Z:/nope/never.csv")


def test_find_existing(tmp_path):
    (tmp_path / f"Some title [{VID}].m4a").write_bytes(b"x")
    assert yi.find_existing(VID, [tmp_path / "missing", tmp_path]) is not None
    assert yi.find_existing("abcdefghijk", [tmp_path]) is None


def test_friendly_errors():
    assert yi.friendly_download_error(Exception("ERROR: [youtube] abc: Private video. Sign in")) == "This video is private."
    assert "unavailable" in yi.friendly_download_error(Exception("ERROR: Video unavailable"))
    assert yi.friendly_download_error(Exception("weird thing")).startswith("Couldn't download")


def test_downloader_skips_already_present(tmp_path, monkeypatch):
    (tmp_path / f"Old [{VID}].m4a").write_bytes(b"x")
    calls = []
    monkeypatch.setattr(yi, "download_audio", lambda *a, **k: calls.append(a) or tmp_path / "x.m4a")
    d = yi.YouTubeDownloader()
    added, repeats = d.add([yi.YouTubeLink("video", VID), yi.YouTubeLink("video", VID)], tmp_path, [])
    assert (added, repeats) == (1, 1)
    d._thread.join(5)
    s = d.snapshot()
    assert s["already"] == 1 and s["done"] == 0 and not s["running"] and calls == []


def test_downloader_reports_failure(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise Exception("ERROR: Video unavailable")

    monkeypatch.setattr(yi, "download_audio", boom)
    d = yi.YouTubeDownloader()
    d.add([yi.YouTubeLink("video", "abcdefghijk")], tmp_path, [])
    d._thread.join(5)
    s = d.snapshot()
    assert s["failed"] == 1 and "unavailable" in s["errors"][0]
    assert "1 failed" in yi.summary_line(s)
