"""LLM service for generating intelligent responses using Claude."""
from __future__ import annotations

from typing import List, Dict, Any
import anthropic

from app.core.config import settings


class LLMService:
    """Service for interacting with Claude API."""

    def __init__(self):
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY.strip())
        self.model = settings.LLM_MODEL
        self.max_tokens = settings.LLM_MAX_TOKENS
        self.temperature = settings.LLM_TEMPERATURE

    def generate_chat_response(
        self,
        *,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        system_prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """
        Generate a chat response using Claude.

        Args:
            user_message: The user's current message
            conversation_history: List of previous messages with {"role": "user"|"assistant", "content": "..."}
            system_prompt: System instructions for Claude
            max_tokens: Override default max tokens
            temperature: Override default temperature
            tools: Optional list of tools Claude can use

        Returns:
            Dictionary with:
            - text: The assistant's response text
            - tool_calls: List of tool calls if any (with name and input)
            - stop_reason: Why the model stopped (end_turn, tool_use, etc.)
        """
        # Build messages list with conversation history + current message
        messages = []

        # Add conversation history
        for msg in conversation_history:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

        # Add current user message
        messages.append({
            "role": "user",
            "content": user_message
        })

        try:
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens or self.max_tokens,
                "temperature": temperature or self.temperature,
                "system": system_prompt,
                "messages": messages
            }

            # Add tools if provided
            if tools:
                kwargs["tools"] = tools

            response = self.client.messages.create(**kwargs)

            # Extract text and tool calls from response
            text_parts = []
            tool_calls = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })

            result_text = " ".join(text_parts) if text_parts else ""

            # If no text and no tool calls, provide default message
            if not result_text and not tool_calls:
                result_text = "I apologize, but I couldn't generate a response. Please try again."

            return {
                "text": result_text,
                "tool_calls": tool_calls,
                "stop_reason": response.stop_reason
            }

        except anthropic.APIError as e:
            return {"text": f"API Error: {str(e)}", "tool_calls": [], "stop_reason": "error"}
        except Exception as e:
            return {"text": f"Error generating response: {str(e)}", "tool_calls": [], "stop_reason": "error"}


# Singleton instance
_llm_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """Get or create the LLM service singleton."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
