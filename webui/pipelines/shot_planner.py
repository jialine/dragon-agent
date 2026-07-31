"""Shot planning & prompt optimization for drama production."""
import json
import subprocess
from pipelines.script_writer import _curl_post, LLM_MODEL


def extract_shots_from_script(script_data):
    """
    Extract all shots from a parsed script JSON.
    Returns: list of {episode, shot_number, scene_desc, characters, camera, dialogue, duration_sec}
    """
    shots = []
    for ep in script_data.get("episodes", []):
        for shot in ep.get("shots", []):
            shots.append({
                "episode": ep.get("episode", 1),
                "shot_number": shot.get("shot_number", len(shots) + 1),
                "scene_desc": shot.get("scene_desc", ""),
                "characters": shot.get("characters", []),
                "camera": shot.get("camera", "中景"),
                "dialogue": shot.get("dialogue", ""),
                "duration_sec": shot.get("duration_sec", 8),
                "transition": shot.get("transition", "cut"),
            })
    return shots


def plan_model_for_shot(shot, characters_available):
    """
    Decide T2V vs R2V based on:
    - Characters in shot have reference images → R2V
    - No reference images available → T2V
    Returns: (model, ref_image_url or None)
    """
    shot_chars = shot.get("characters", [])
    primary_char = shot_chars[0] if shot_chars else None

    # Check if primary character has a reference image
    for c in characters_available:
        if c["name"] == primary_char and c.get("portrait_oss"):
            return "happyhorse-1.1-r2v", c["portrait_oss"]

    # Fallback to T2V
    return "happyhorse-1.1-t2v", None


def build_shot_prompts(shots, characters, project_name=""):
    """
    Build optimized prompts for all shots.
    Returns: list of shot objects with model, prompt_optimized, ref_image, duration
    """
    char_context = "\n".join([
        f"{c['name']}: {c.get('description', '')} (type={c.get('role_type', 'human')})"
        for c in characters
    ])

    planned_shots = []
    for shot in shots:
        model, ref_image = plan_model_for_shot(shot, characters)
        planned_shots.append({
            **shot,
            "model": model,
            "ref_image": ref_image,
            "prompt_optimized": shot["scene_desc"],  # will be optimized later
        })

    return planned_shots


def optimize_shot_prompt(shot_desc, model, characters_context=""):
    """Optimize a single shot prompt for HappyHorse."""
    from pipelines.script_writer import optimize_prompt
    return optimize_prompt(shot_desc, model, characters_context)
