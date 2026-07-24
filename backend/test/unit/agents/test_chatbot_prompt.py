from yuxi.agents.buildin.chatbot.prompt import PROMPT


def test_chatbot_prompt_does_not_duplicate_html_preview_skill_instructions():
    assert "html:preview" not in PROMPT
