from __future__ import annotations

from dataclasses import dataclass

from pytest import MonkeyPatch

from utteran.japanese_boundaries import japanese_phrase_boundaries


@dataclass
class FakeMorpheme:
    text: str
    major: str

    def surface(self) -> str:
        return self.text

    def part_of_speech(self) -> tuple[str, str, str, str, str, str]:
        return (self.major, "*", "*", "*", "*", "*")


class FakeTokenizer:
    def tokenize(self, _text: str, _mode: object) -> list[FakeMorpheme]:
        return [
            FakeMorpheme("特に", "副詞"),
            FakeMorpheme("本文", "名詞"),
            FakeMorpheme("の", "助詞"),
            FakeMorpheme("例", "名詞"),
        ]


def test_phrase_boundaries_include_leading_adverb_boundary(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "utteran.japanese_boundaries._load_sudachi",
        lambda: (FakeTokenizer(), object(), object()),
    )

    assert japanese_phrase_boundaries("特に本文の例") == {0, 2, 6}
