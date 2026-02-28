# Conversation Context & Memory Management ✅

## Overview

The Context Management system ensures that conversations stay within Claude's token limits while maintaining continuity and context. It automatically summarizes older messages and intelligently manages the conversation window.

---

## Features

### 1. **Token Tracking**
- Estimates token usage for all messages
- Monitors total context window usage
- Prevents exceeding model limits

### 2. **Automatic Summarization**
- Triggers when conversation approaches token limit (150K tokens)
- Uses Claude to generate intelligent summaries
- Preserves key information from older messages

### 3. **Smart Message Retention**
- Keeps most recent messages intact
- Summarizes older conversation history
- Maintains conversation continuity

### 4. **Context Injection**
- Injects summaries into system prompt
- Provides historical context without full message history
- Seamless for the user

---

## Architecture

### Core Components

1. **`ContextManager`** - Main service class
2. **Token Estimation** - Rough token counting (~4 chars/token)
3. **Summarization Engine** - Uses Claude to compress history
4. **Window Management** - Keeps recent + summarizes old

### Files

- **Service**: `apps/api/app/services/context_manager.py`
- **Integration**: `apps/api/app/services/chat.py`

---

## How It Works

### 1. Token Counting

Every message is counted:
```python
context_manager = get_context_manager()

# Estimate tokens for text
tokens = context_manager.estimate_tokens("Hello world")  # ~3 tokens

# Count message tokens
message = {"role": "user", "content": "What is the revenue?"}
msg_tokens = context_manager.count_message_tokens(message)  # ~6 tokens

# Count all messages
total = context_manager.count_messages_tokens(messages)
```

### 2. Context Window Management

Before sending to LLM, conversation is processed:

```python
# Manages context automatically
result = context_manager.manage_context_window(
    messages=conversation_history,
    system_prompt=system_prompt,
    keep_recent_count=10,  # Keep last 10 messages (5 turns)
)

# Result contains:
# - messages: Processed messages to use (recent ones)
# - summary: Summary of older messages (if any)
# - was_summarized: Whether summarization occurred
# - total_tokens: Estimated total tokens after management
```

### 3. Summarization Strategy

When conversation exceeds 150K tokens:

1. **Keep Recent** - Last 10 messages (5 user-assistant turns) retained
2. **Summarize Old** - Everything before that is summarized
3. **Inject Summary** - Summary added to system prompt
4. **Send to LLM** - Recent messages + summary context

**Example:**

```
Original: 50 messages (160K tokens)
After Management:
- Summary: First 40 messages compressed to ~2K tokens
- Recent: Last 10 messages kept as-is (~15K tokens)
- Total: ~17K tokens (vs 160K)
```

### 4. Summary Quality

Claude generates intelligent summaries focusing on:
- Key questions asked by user
- Important data points discovered
- SQL queries executed and results
- Calculations performed
- Patterns and trends identified

**Example Summary:**
```
[Summary of 40 messages]

User explored revenue performance data with the following key findings:
• Asked about regional revenue distribution - North America leads with $170K
• Calculated revenue projections with 15% growth rate
• Analyzed customer segments - Enterprise has highest avg profit ($42.5K)
• Identified top-performing products and sales trends
• Generated multiple SQL queries for segment analysis
```

---

## Configuration

### Token Limits

Default configuration in `context_manager.py`:

```python
MAX_CONTEXT_TOKENS = 180_000  # Conservative limit (Claude has 200K)
SUMMARY_TRIGGER_TOKENS = 150_000  # When to start summarizing
CHARS_PER_TOKEN = 4  # Rough estimation heuristic
```

### Message Retention

In `chat.py`:

```python
keep_recent_count=10  # Keep last 10 messages (5 turns)
```

You can adjust this based on your needs:
- **Higher** = More context but more tokens
- **Lower** = Less context but fewer tokens

---

## Integration Example

The context manager is automatically used in the chat service:

```python
from app.services.context_manager import get_context_manager

# In _generate_agentic_response():

# 1. Get conversation history
conversation_history = [...]  # Load from DB

# 2. Manage context window
context_manager = get_context_manager()
context_result = context_manager.manage_context_window(
    messages=conversation_history,
    system_prompt=base_system_prompt,
    keep_recent_count=10,
)

# 3. Use managed history
managed_history = context_result["messages"]
conversation_summary = context_result.get("summary")

# 4. Inject summary if needed
if conversation_summary:
    system_prompt = context_manager.inject_summary_into_system_prompt(
        base_system_prompt,
        conversation_summary
    )

# 5. Generate response with managed context
response = llm_service.generate_chat_response(
    user_message=user_message,
    conversation_history=managed_history,  # Uses managed history
    system_prompt=system_prompt,  # Includes summary if needed
    tools=llm_tools,
)
```

---

## Metadata Tracking

Each assistant message includes context management metadata:

```json
{
  "context": {
    "context_management": {
      "was_summarized": true,
      "total_tokens": 17500,
      "summarized_count": 40,
      "retained_count": 10
    }
  }
}
```

This helps track:
- Whether summarization occurred
- Token usage
- How many messages were summarized vs retained

---

## Benefits

### ✅ **Unlimited Conversations**
- No practical limit on conversation length
- Automatically manages token limits
- Users can have lengthy analysis sessions

### ✅ **Context Preservation**
- Summaries maintain key information
- Recent messages always available
- Continuity across long conversations

### ✅ **Cost Optimization**
- Reduces token usage for long conversations
- Summaries are much smaller than full history
- Lower API costs

### ✅ **Performance**
- Faster responses (less context to process)
- Reduced latency
- More efficient API usage

### ✅ **Transparent**
- Automatic - no user intervention needed
- Seamless experience
- Metadata available for debugging

---

## Testing

### Manual Testing

Test with a long conversation:

```bash
# Test script that creates a long conversation
/tmp/test_context_management.sh
```

What to test:
1. **Short conversations** - Should NOT trigger summarization
2. **Long conversations** - Should automatically summarize after ~40 messages
3. **Summary quality** - Check that key info is preserved
4. **Context continuity** - Verify Claude can reference old context via summary

### Via API

```bash
TOKEN="your_token_here"
SESSION_ID="your_session_id"

# Send many messages
for i in {1..50}; do
  curl -X POST "http://localhost:8001/api/v1/chat/sessions/$SESSION_ID/messages" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"content\": \"Question $i: What is the total revenue?\"}"
done

# Check last message context for summarization metadata
curl "http://localhost:8001/api/v1/chat/sessions/$SESSION_ID/messages" \
  -H "Authorization: Bearer $TOKEN" | jq '.[-1].context.context_management'
```

### Expected Behavior

**First 30-40 messages:**
```json
{
  "was_summarized": false,
  "total_tokens": 45000
}
```

**After 40+ messages:**
```json
{
  "was_summarized": true,
  "total_tokens": 17500,
  "summarized_count": 40,
  "retained_count": 10
}
```

---

## Advanced Features

### Custom Retention Strategies

You can customize how messages are retained:

```python
# Keep more recent messages (better context, more tokens)
context_result = context_manager.manage_context_window(
    messages=conversation_history,
    system_prompt=system_prompt,
    keep_recent_count=20,  # Keep 20 messages instead of 10
)

# Keep fewer messages (less context, fewer tokens)
context_result = context_manager.manage_context_window(
    messages=conversation_history,
    system_prompt=system_prompt,
    keep_recent_count=6,  # Keep only 6 messages
)
```

### Fallback Summarization

If Claude API fails during summarization, the system falls back to a simple summary:

```python
def _simple_summary(self, messages: List[Dict[str, str]]) -> str:
    """Create a simple summary without LLM."""
    # Extracts user questions and assistant responses
    # Returns basic text summary
```

This ensures the system continues working even if summarization fails.

---

## Troubleshooting

### Issue: "Conversations getting cut off too early"

**Solution**: Increase `SUMMARY_TRIGGER_TOKENS`:

```python
# In context_manager.py
SUMMARY_TRIGGER_TOKENS = 170_000  # Higher threshold
```

### Issue: "Summaries losing important context"

**Solution**: Increase `keep_recent_count`:

```python
# In chat.py
keep_recent_count=15  # Keep more recent messages
```

### Issue: "Token counts seem inaccurate"

**Note**: Token estimation is approximate (~4 chars/token). For production, consider using `tiktoken`:

```python
import tiktoken

def estimate_tokens(self, text: str) -> int:
    encoding = tiktoken.encoding_for_model("claude-3-sonnet")
    return len(encoding.encode(text))
```

---

## Future Enhancements

### Potential Improvements:

1. **Semantic Importance Scoring**
   - Keep messages with high importance (not just recent)
   - Preserve breakthrough insights even if old

2. **Multi-Level Summarization**
   - Level 1: Last 10 messages (full)
   - Level 2: Previous 20 messages (brief summary)
   - Level 3: Earlier messages (very brief)

3. **User-Controlled Settings**
   - Allow users to set retention preferences
   - Per-session context management settings

4. **Conversation Branches**
   - Handle forking conversations
   - Multiple summary threads

5. **Accurate Token Counting**
   - Use `tiktoken` or official tokenizer
   - Precise token tracking

6. **Compression Metrics**
   - Track compression ratios
   - Analyze summary quality
   - Optimize trigger thresholds

---

## Summary

The Context Management system provides:

✅ **Automatic** token limit handling
✅ **Intelligent** conversation summarization
✅ **Seamless** user experience
✅ **Cost-effective** API usage
✅ **Scalable** to very long conversations

It's a critical component for production-ready LLM chat applications, ensuring conversations can continue indefinitely while staying within model constraints.

---

## Support

For issues or questions:
- Check context_management metadata in message context
- Verify token counts are being tracked
- Test with progressively longer conversations
- Review summary quality in system prompts

**Happy chatting! 💬**
