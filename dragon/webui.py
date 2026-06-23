#!/usr/bin/env python3
"""Dragon Agent — Gradio Web UI"""
import gradio as gr
import requests
import os

API = os.getenv("DRAGON_API_URL", "http://localhost:8000")


def chat_fn(message, history):
    messages = []
    for h in history:
        messages.append({"role": "user", "content": h[0]})
        if h[1]:
            messages.append({"role": "assistant", "content": h[1]})
    messages.append({"role": "user", "content": message})
    try:
        r = requests.post(f"{API}/v1/chat", json={"messages": messages}, timeout=60)
        if r.status_code == 200:
            return r.json().get("content", "No response")
    except Exception as e:
        return f"Error: {e}"


def get_status():
    try:
        r = requests.get(f"{API}/health", timeout=5)
        if r.status_code == 200:
            data = r.json()
            lines = []
            for comp, info in data.get("components", {}).items():
                status = info.get("status", "unknown")
                emoji = "✅" if status in ("ready", "healthy") else "❌"
                lines.append(f"{emoji} **{comp}**: {status}")
            return "\n".join(lines) or "Connected"
    except Exception as e:
        return f"❌ Cannot connect: {e}"


def create_ui():
    with gr.Blocks(title="🐉 Dragon Agent", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 🐉 Dragon Agent Management Panel")
        with gr.Tab("💬 Chat"):
            gr.ChatInterface(fn=chat_fn, title="Chat")
        with gr.Tab("📊 Status"):
            btn = gr.Button("Refresh")
            out = gr.Markdown("Click Refresh")
            btn.click(fn=get_status, outputs=out)
        with gr.Tab("🔧 Tools"):
            tools = "| Tool | Desc |\n|---|---|\n"
            for t in [
                ("web_search", "Multi-engine"),
                ("tts", "Text-to-speech"),
                ("vision_analyze", "Image analysis"),
                ("image_generate", "Image gen"),
                ("geocode", "Address→coords"),
                ("get_route", "Routing"),
                ("search_poi", "POI search"),
                ("browser_open", "Web browser"),
                ("email_send", "Send email"),
                ("kanban_create_board", "Kanban"),
            ]:
                tools += f"| `{t[0]}` | {t[1]} |\n"
            gr.Markdown(tools)
    return demo


if __name__ == "__main__":
    demo = create_ui()
    demo.queue().launch(server_name="0.0.0.0", server_port=7860, share=False)
