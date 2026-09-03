"""Tests for drama-pipeline/scripts/h3_prompt.py — H3 Ref2VA 六段式构造器."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "drama-pipeline", "scripts"))

from h3_prompt import build_ref2va_prompt, build_ref2va_prompt_from_shot, lang_tag

SUBJECTS = [
    {"name": "Silverback",
     "appearance": "a massive silverback gorilla with silver-grey back fur, golden eyes, a cranial implant chip, and a scar on the left brow",
     "ref": 1},
    {"name": "Lu Zheng",
     "appearance": "a young man with short black hair and a black tactical jacket",
     "ref": 2},
]


def _songuan_dialogue():
    return build_ref2va_prompt(
        subjects=SUBJECTS,
        style="Live-action, cinematic, cold blue laboratory lighting.",
        action="Silverback steps forward and glares down, while Lu Zheng stands his ground.",
        dialogue=[
            {"speaker": "Silverback", "text": "你一个人来的。", "language": "Chinese"},
            {"speaker": "Lu Zheng", "text": "我为答案而来。", "language": "中文"},
        ],
        soundscape="Low ventilation hum with distant facility alarms.",
        no_bgm=True,
    )


class TestSixSections:
    def test_all_six_sections_in_order(self):
        p = build_ref2va_prompt(SUBJECTS, "Live-action.", "X walks.",
                                soundscape="N/A", no_bgm=True)
        idx = [p.index(k) for k in (
            "subject_definitions:", "summary:", "retention_analysis:",
            "detailed_description:", "overall_soundscape:", "non_diegetic_music:")]
        assert idx == sorted(idx), "六段顺序必须固定"

    def test_subject_picture_binding(self):
        p = build_ref2va_prompt(SUBJECTS, "Live-action.", "X walks.", soundscape="N/A")
        assert "<Subject 1> is Silverback" in p
        assert "<Subject 2> is Lu Zheng" in p
        assert "in <Picture 1>." in p
        assert "in <Picture 2>." in p

    def test_no_bgm_is_na(self):
        p = build_ref2va_prompt(SUBJECTS, "Live-action.", "X walks.", soundscape="N/A", no_bgm=True)
        assert p.rstrip().endswith("non_diegetic_music:\nN/A")

    def test_action_bare_name_replaced(self):
        # action 里的裸名必须被替换成 <Subject N>, 保证 label 引用一致
        p = build_ref2va_prompt(
            SUBJECTS, "Live-action.", "Silverback steps forward, Lu Zheng retreats.",
            soundscape="N/A")
        assert "<Subject 1> steps forward, <Subject 2> retreats." in p
        # 裸名不再出现在 detailed_description 的 action 句
        dd = p.split("detailed_description:")[1]
        assert "Silverback steps" not in dd
        assert "Lu Zheng retreats" not in dd


class TestDialogue:
    def test_chinese_dialogue_tag(self):
        p = _songuan_dialogue()
        assert "<d>[Chinese] 你一个人来的。</d>" in p
        assert "<d>[Chinese] 我为答案而来。</d>" in p

    def test_english_dialogue_tag(self):
        p = build_ref2va_prompt(
            SUBJECTS, "Live-action.", "A talks.",
            dialogue=[{"speaker": "Silverback", "text": "You came alone.", "language": "English"}],
            soundscape="N/A")
        assert "<d>[English] You came alone.</d>" in p

    def test_speaker_id_sequential(self):
        p = build_ref2va_prompt(
            SUBJECTS, "Live-action.", "Silverback talks to Lu Zheng.",
            dialogue=[
                {"speaker": "Silverback", "text": "I came alone.", "language": "English"},
                {"speaker": "Lu Zheng", "text": "Prove it.", "language": "English"},
                {"speaker": "Silverback", "text": "Fine.", "language": "English"},
            ],
            soundscape="N/A")
        assert "<Subject 1> (S1) says" in p
        assert "<Subject 2> (S2) says" in p
        # 第三个对白复用 S1, 不重新编号
        assert p.count("(S1) says") == 2
        assert "(S3)" not in p

    def test_speaker_not_in_subjects_raises(self):
        import pytest
        with pytest.raises(ValueError):
            build_ref2va_prompt(
                SUBJECTS, "Live-action.", "A talks.",
                dialogue=[{"speaker": "Nobody", "text": "hi", "language": "English"}],
                soundscape="N/A")


class TestLangTag:
    def test_variants(self):
        assert lang_tag("Chinese") == "Chinese"
        assert lang_tag("中文") == "Chinese"
        assert lang_tag("English") == "English"
        assert lang_tag("英文") == "English"


class TestFromShot:
    def test_minimal_shot(self):
        shot = {
            "subjects": SUBJECTS,
            "style": "Live-action.",
            "action": "Silverback nods.",
            "soundscape": "N/A",
        }
        p = build_ref2va_prompt_from_shot(shot)
        assert p.startswith("subject_definitions:")
        assert "non_diegetic_music:\nN/A" in p