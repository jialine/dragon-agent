"""Shot-to-shot continuity engine for drama production.

Ensures visual coherence across consecutive shots:
- Character appearance consistency (outfit, hair, props)
- Scene/setting continuity (lighting, time-of-day, location)
- Action flow (what happened between shots)
- Prop tracking (objects carried between shots)
"""

from pipelines.script_writer import _curl_post, LLM_MODEL


def build_continuity_context(shots, characters):
    """
    For each shot, build a continuity context from previous shots.
    Returns list of {shot_index, prev_context, char_states, scene_context}
    """
    # Track character state across shots: {char_name: {outfit, holding, location, last_action}}
    char_states = {c["name"]: {"outfit": c.get("description", ""), "holding": "", "location": "", "last_action": ""}
                   for c in characters}

    # Track current scene
    current_scene = {"location": "", "time_of_day": "", "lighting": "", "mood": ""}

    continuity_chain = []

    for i, shot in enumerate(shots):
        scene_desc = shot.get("scene_desc", "")
        shot_chars = shot.get("characters", [])

        # Build previous context
        prev_context = ""
        if i > 0:
            prev_shot = shots[i - 1]
            prev_context = f"上一镜: {prev_shot.get('scene_desc', '')}"
            # Include character states
            prev_char_info = []
            for cn in shot_chars:
                if cn in char_states:
                    cs = char_states[cn]
                    prev_char_info.append(
                        f"{cn}: 服装={cs['outfit'][:30]}, 手持={cs['holding'] or '无'}, "
                        f"位置={cs['location'] or current_scene.get('location', '未知')}, "
                        f"最后动作={cs['last_action'][:30]}"
                    )
            if prev_char_info:
                prev_context += " | " + "; ".join(prev_char_info)

        # Scene context
        scene_context = f"场景: {current_scene.get('location', '同上')}, "
        scene_context += f"时间: {current_scene.get('time_of_day', '同上')}, "
        scene_context += f"光线: {current_scene.get('lighting', '同上')}, "
        scene_context += f"氛围: {current_scene.get('mood', '同上')}"

        continuity_chain.append({
            "shot_index": i,
            "shot": shot,
            "prev_context": prev_context,
            "char_states": {k: dict(v) for k, v in char_states.items()},
            "scene_context": scene_context,
        })

        # Update state based on this shot (for next iteration)
        _update_state(scene_desc, shot_chars, char_states, current_scene)

    return continuity_chain


def _update_state(scene_desc, shot_chars, char_states, current_scene):
    """Extract state changes from scene description."""
    desc_lower = scene_desc

    # Detect location changes
    for keyword, location in [
        ("实验室", "实验室"), ("主控室", "主控室"), ("走廊", "走廊"),
        ("电梯", "电梯竖井"), ("隔离舱", "隔离舱"), ("室外", "室外"),
        ("办公室", "办公室"), ("街道", "街道"), ("天台", "天台"),
    ]:
        if keyword in scene_desc:
            current_scene["location"] = location
            break

    # Detect time of day
    for keyword, tod in [
        ("深夜", "深夜"), ("夜晚", "夜晚"), ("黄昏", "黄昏"),
        ("清晨", "清晨"), ("白天", "白天"), ("黎明", "黎明"),
    ]:
        if keyword in scene_desc:
            current_scene["time_of_day"] = tod
            break

    # Detect lighting
    for keyword, light in [
        ("红光", "红色应急灯光"), ("蓝光", "蓝色应急灯光"),
        ("冷白色", "冷白色灯光"), ("暗红", "暗红色灯光"),
        ("频闪", "红色警报频闪"), ("荧光", "荧光灯"),
    ]:
        if keyword in scene_desc:
            current_scene["lighting"] = light
            break

    # Detect mood
    for keyword, mood in [
        ("警报", "紧张"), ("紧急", "紧急"), ("宁静", "宁静"),
        ("压抑", "压抑"), ("激烈", "激烈"), ("悲伤", "悲伤"),
    ]:
        if keyword in scene_desc:
            current_scene["mood"] = mood
            break

    # Update character states
    for cn in shot_chars:
        if cn not in char_states:
            continue
        # Extract last action
        actions = ["起身", "砸下", "按下", "走出", "转身", "抬头", "低头",
                   "蹲坐", "站立", "握紧", "开口", "说话", "伸手", "盯着", "扫描"]
        for act in actions:
            if act in scene_desc:
                char_states[cn]["last_action"] = act
                break
        char_states[cn]["location"] = current_scene.get("location", "")


def check_shot_coherence(shots, continuity_chain):
    """
    Use LLM to review all shots for coherence issues.
    Returns list of {shot_id, issue_type, description, severity}
    """
    if len(shots) < 2:
        return []

    # Build shot summary
    shot_summary = ""
    for i, shot in enumerate(shots):
        ctx = continuity_chain[i] if i < len(continuity_chain) else {}
        shot_summary += (
            f"镜{i + 1}: {shot.get('scene_desc', '')[:80]} "
            f"[角色:{','.join(shot.get('characters', []))}] "
            f"[镜头:{shot.get('camera', '中景')}] "
            f"[{ctx.get('scene_context', '')[:60]}]\n"
        )

    system = """你是镜头连续性审查员。检查以下分镜序列，找出不连贯之处：
1. 角色外观突变（同一角色在不同镜中服装/发型/外貌描述不一致）
2. 场景跳跃（没有过渡就切换到完全不同的地点/时间）
3. 动作断层（前一镜结束的动作和后一镜开始的动作接不上）
4. 道具消失（角色手里的东西突然不见了）
5. 光线/色调突变（无理由的灯光变化）

返回JSON数组，每个问题一个对象。如无问题返回空数组[]。
[{ "shot_range": "镜3→镜4", "issue_type": "场景跳跃", "description": "镜3在实验室内，镜4突然跳到室外街道，缺少过渡", "severity": "high" }]"""

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"审查以下{len(shots)}个镜头的连续性：\n{shot_summary}"}
        ],
        "temperature": 0.2,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"}
    }

    import json, re
    try:
        resp = _curl_post(payload, timeout=60)
        content = resp["choices"][0]["message"]["content"]
        content = re.sub(r"```json\s*|```", "", content).strip()
        idx = content.find("[")
        if idx >= 0:
            content = content[idx:]
            idx = content.rfind("]")
            if idx >= 0:
                content = content[:idx + 1]
        issues = json.loads(content)
        if isinstance(issues, dict) and "issues" in issues:
            issues = issues["issues"]
        return issues if isinstance(issues, list) else []
    except Exception as e:
        return [{"shot_range": "N/A", "issue_type": "review_error", "description": str(e), "severity": "low"}]


def inject_continuity_constraints(shots, continuity_chain, char_descriptions):
    """
    For each shot, inject continuity constraints into the prompt.
    - "same outfit as shot N"
    - "continuous action from previous shot"
    - "same lighting as established"
    """
    enriched_shots = []

    for i, shot in enumerate(shots):
        ctx = continuity_chain[i] if i < len(continuity_chain) else {}
        constraints = []

        # Character appearance lock
        shot_chars = shot.get("characters", [])
        for cn in shot_chars:
            for cd in char_descriptions:
                if cd.get("name") == cn:
                    constraints.append(f"{cn}外观与镜1一致")
                    break

        # Scene continuity
        if i > 0:
            prev_shot = shots[i - 1]
            prev_chars = set(prev_shot.get("characters", []))
            curr_chars = set(shot_chars)

            # Same characters → action continuity
            if prev_chars & curr_chars:
                constraints.append("动作紧接上一镜")

            # Same location → lighting continuity
            prev_ctx = continuity_chain[i - 1] if i - 1 < len(continuity_chain) else {}
            curr_scene = ctx.get("scene_context", "")
            prev_scene = prev_ctx.get("scene_context", "")
            if curr_scene[:20] == prev_scene[:20]:
                constraints.append("光线氛围与上一镜一致")

        # No glasses rule for scientists (global constraint)
        for cn in shot_chars:
            for cd in char_descriptions:
                if cd.get("name") == cn and "科学家" in cd.get("description", ""):
                    constraints.append(f"{cn}不戴眼镜")

        enriched_shots.append({
            **shot,
            "continuity_constraints": constraints,
            "continuity_context": ctx.get("prev_context", ""),
        })

    return enriched_shots
