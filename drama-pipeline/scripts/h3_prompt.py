#!/usr/bin/env python3
"""
H3 Ref2VA 官方六段式 prompt 构造器
===================================
按 MiniMax H3 官方《Full-Reference Mode Rewrite Output Format Guide》,
把结构化镜头信息确定性组装成六段式 prompt, 让参考图绑定关系准确传进模型,
从而锁死角色一致性。

六段式结构 (顺序固定):
  1. subject_definitions   定义每个 <Subject N> 对应哪张 <Picture N> + 外貌
  2. summary               任务类型前缀 + 一句话总结
  3. retention_analysis    每个 label 的保真关系 (fully_preserved 等)
  4. detailed_description  风格句 + [Shot N] 播放顺序描述 + 对白 <d>[语言]</d>
  5. overall_soundscape    环境音/物理音总结
  6. non_diegetic_music    纯 BGM (短剧铁律 → N/A)

无第三方依赖, 可独立单测/CLI。

CLI 用法:
  python3 h3_prompt.py --json shot.json     # 读结构化 shot, 打印六段式
  python3 h3_prompt.py --probe             # 自检: 打印一个示例六段式
"""

import json
import argparse

# 语言标签标准化 (官方 <d> 内用 [Language] 标签)
_LANG = {
    "chinese": "Chinese", "中文": "Chinese", "cn": "Chinese", "zh": "Chinese",
    "english": "English", "英文": "English", "en": "English",
}


def lang_tag(language):
    """把口头/简写语言名规范成官方 <d> 标签."""
    return _LANG.get(str(language).strip().lower(), str(language).strip() or "Chinese")


def build_ref2va_prompt(subjects, style, action, dialogue=None,
                        soundscape=None, no_bgm=True):
    """组装官方 full-reference 六段式 prompt (返回纯文本字符串).

    参数:
      subjects : [{"name", "ref", "appearance"}, ...]
          name        角色/对象英文短名 (如 "Silverback"), 用于 speaker 匹配
          ref         参考图序号 (1-based, 对应 ref_images[ref-1])
          appearance  该角色在这张 ref 图里的外貌 (英文, 越具体越好)
      style    : 英文风格句 (1-2 句, 如 "Live-action, cinematic, dark moody lighting.")
      action   : 英文动作描述 (不含对白), 可已含 <Subject N> 引用
      dialogue : [{"speaker", "text", "language"}, ...]
          speaker 必须匹配某个 subjects[].name
      soundscape : 英文环境音/物理音总结 (1-3 句)
      no_bgm  : True → non_diegetic_music: N/A (短剧无 BGM 铁律)

    返回: 六段式纯文本 (直接喂给 MiniMaxH3ReferenceToVideo.prompt)
    """
    # 1. subject_definitions
    lines = ["subject_definitions:"]
    for i, s in enumerate(subjects, 1):
        if s.get("ref"):
            lines.append(
                f"<Subject {i}> is {s['name']}, {s['appearance'].strip().rstrip('.')}, "
                f"in <Picture {s['ref']}>."
            )
        else:
            lines.append(
                f"<Subject {i}> is {s['name']}, {s['appearance'].strip().rstrip('.')}."
            )
    lines.append("")

    # 2. summary
    names = " and ".join(f"<Subject {i}>" for i in range(1, len(subjects) + 1))
    lines.append("summary:")
    lines.append(
        f"[reference generation] The target video shows {names} performing the "
        f"described action, with each subject's appearance generated from its "
        f"reference picture."
    )
    lines.append("")

    # 3. retention_analysis
    lines.append("retention_analysis:")
    for i, s in enumerate(subjects, 1):
        if s.get("ref"):
            lines.append(
                f"<Subject {i}> (appears in [Shot 1]): fully_preserved - "
                f"the appearance, identity, and key features from <Picture {s['ref']}> are retained."
            )
        else:
            lines.append(
                f"<Subject {i}> (appears in [Shot 1]): fully_preserved - "
                f"the described appearance is retained."
            )
    lines.append("")

    # 4. detailed_description
    lines.append("detailed_description:")
    lines.append(style.strip().rstrip())
    # 首次外观锚定 (独立成句, 句号断句)
    intro = ". ".join(
        f"<Subject {i}>, {s['appearance'].strip().rstrip('.')}"
        for i, s in enumerate(subjects, 1)
    )
    # action 里裸角色名 → <Subject N>, 保证 label 引用一致
    act = action.strip().rstrip('.')
    for i, s in enumerate(subjects, 1):
        act = act.replace(s["name"], f"<Subject {i}>")
    shot_parts = [f"[Shot 1] {intro}. {act}."]
    if dialogue:
        # 分配 speaker ID: 按对白出现顺序
        sid = {}
        for d in dialogue:
            name = d["speaker"]
            subj_idx = next((i + 1 for i, s in enumerate(subjects) if s["name"] == name), None)
            if subj_idx is None:
                raise ValueError(f"dialogue speaker '{name}' 不在 subjects 中")
            if name not in sid:
                sid[name] = len(sid) + 1
            tag = lang_tag(d.get("language"))
            shot_parts.append(
                f"<Subject {subj_idx}> (S{sid[name]}) says, <d>[{tag}] {d['text']}</d>."
            )
    lines.append(" ".join(shot_parts))

    # 5. overall_soundscape
    lines.append("")
    lines.append("overall_soundscape:")
    lines.append(soundscape.strip().rstrip() if soundscape else "N/A")

    # 6. non_diegetic_music
    lines.append("")
    lines.append("non_diegetic_music:")
    lines.append("N/A" if no_bgm else "...")

    return "\n".join(lines)


def build_ref2va_prompt_from_shot(shot):
    """从结构化 shot dict 生成六段式 (供 h3_batch_gen 调用)."""
    return build_ref2va_prompt(
        subjects=shot["subjects"],
        style=shot.get("style", "Live-action, cinematic."),
        action=shot.get("action", ""),
        dialogue=shot.get("dialogue"),
        soundscape=shot.get("soundscape"),
        no_bgm=shot.get("no_bgm", True),
    )


# ==================== CLI ====================
_PROBE_SHOT = {
    "subjects": [
        {"name": "Silverback", "ref": 1,
         "appearance": "a massive silverback gorilla with silver-grey back fur, golden eyes, a cranial implant chip, and a scar on the left brow"},
        {"name": "Lu Zheng", "ref": 2,
         "appearance": "a young man with short black hair and a black tactical jacket"},
    ],
    "style": "Live-action, cinematic, cold blue laboratory lighting.",
    "action": "Silverback steps forward and glares down, while Lu Zheng stands his ground and raises a warning hand.",
    "dialogue": [
        {"speaker": "Silverback", "text": "You came alone.", "language": "English"},
        {"speaker": "Lu Zheng", "text": "I came for answers.", "language": "English"},
    ],
    "soundscape": "Low ventilation hum with distant facility alarms echoing through the corridor.",
}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="H3 Ref2VA 六段式 prompt 构造器")
    ap.add_argument("--json", help="结构化 shot JSON 路径")
    ap.add_argument("--probe", action="store_true", help="打印示例六段式")
    args = ap.parse_args()

    if args.probe:
        print(build_ref2va_prompt_from_shot(_PROBE_SHOT))
    elif args.json:
        with open(args.json) as f:
            shot = json.load(f)
        print(build_ref2va_prompt_from_shot(shot))
    else:
        ap.print_help()