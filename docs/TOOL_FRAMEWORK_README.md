# Tool Execution Framework ✅

## Overview

The Tool Execution Framework provides a flexible, extensible system for agents to execute various operations through Claude's tool use feature. The framework includes three built-in tools and makes it easy to add custom tools.

---

## Architecture

### Core Components

1. **`Tool` (Base Class)**: Abstract base for all tools
2. **`ToolRegistry`**: Central registry for managing available tools
3. **`ToolResult`**: Standardized response format
4. **Built-in Tools**:
   - `SQLQueryTool` - Execute SQL queries on datasets
   - `CalculatorTool` - Perform mathematical calculations
   - `DataSummaryTool` - Get statistical summaries
   - `EntityExtractionTool` - Extract entities from text into knowledge graph
   - `KnowledgeSearchTool` - Search knowledge graph entities

### Files

- **Framework**: `apps/api/app/services/tool_executor.py`
- **Integration**: `apps/api/app/services/chat.py`

---

## Built-in Tools

### 1. SQL Query Tool

Execute SQL queries on datasets using DuckDB.

**Capabilities:**
- SELECT queries only (read-only)
- Automatic LIMIT enforcement (max 1000 rows)
- SQL injection prevention
- Returns columns, rows, and row count

**Example Usage:**
```
User: "What region has the highest revenue?"
Claude: Generates SQL: SELECT region, SUM(revenue) FROM dataset GROUP BY region ORDER BY SUM(revenue) DESC
Result: North America: $170,000
```

**Test:**
```bash
# Ask: "What is the average profit by customer segment?"
# Claude will automatically generate and execute SQL
```

---

### 2. Calculator Tool 🆕

Perform safe mathematical calculations.

**Capabilities:**
- Basic arithmetic operations (+, -, *, /, parentheses)
- Sandboxed evaluation (no dangerous operations)
- Clear error messages

**Example Usage:**
```
User: "If North America revenue is 170000 and they get a 15% increase, what would the new revenue be?"
Claude: Uses calculator: 170000 * 1.15
Result: $195,500
```

**Test:**
```bash
# Ask: "Calculate (100 + 50) * 2"
# Claude will use the calculator tool
```

---

### 3. Data Summary Tool 🆕

Get statistical summaries of numeric columns.

**Capabilities:**
- Summary for specific column (avg, min, max)
- Summary for all numeric columns
- Fast access to statistics without SQL

**Example Usage:**
```
User: "Give me a statistical summary of the revenue column"
Claude: Uses data_summary tool with column="revenue"
Result:
- Average: $99,700
- Min: $45,000
- Max: $152,000
```

**Test:**
```bash
# Ask: "What are the revenue statistics?"
# Claude will use the data_summary tool
```

---

### 4. Entity Extraction Tool

Extract people, companies, and concepts from text into the knowledge graph.

**Capabilities:**
- Extracts entities (people, organizations, locations, products) from free text
- Wraps `knowledge_extraction_service.extract_from_content()`
- Supports optional entity_schema for structured extraction
- Returns list of extracted entity dicts with types and confidence

**Parameters:**
- `content` (required) - Text content to extract entities from
- `content_type` (default: "plain_text") - Type of content
- `entity_schema` (optional) - Schema to guide extraction

**Example Usage:**
```
User: "Extract contacts from these conference notes"
Agent: Extracts entities via knowledge_extraction_service
Result: [{name: "John Smith", type: "person", confidence: 0.95}, ...]
```

---

### 5. Knowledge Search Tool

Search and browse the knowledge graph for entities.

**Capabilities:**
- Searches knowledge_entities by name, type, and description
- Wraps `knowledge.search_entities()` with tenant isolation
- Supports filtering by entity_type
- Returns matching entities ranked by relevance

**Parameters:**
- `query` (required) - Search query
- `entity_type` (optional) - Filter by type (person, organization, product, etc.)

**Example Usage:**
```
User: "Find all companies in the knowledge graph"
Agent: Searches knowledge graph with entity_type="organization"
Result: [{name: "Acme Corp", type: "organization"}, ...]
```

---

## How It Works

### 1. Tool Registration

When a chat session starts, the framework registers available tools:

```python
# In chat.py
tool_registry = get_tool_registry()
tool_registry.register(SQLQueryTool(dataset_service, dataset))
tool_registry.register(CalculatorTool())
tool_registry.register(DataSummaryTool(dataset_service, dataset))
```

### 2. Schema Generation

Tool schemas are passed to Claude:

```python
llm_tools = tool_registry.get_all_schemas()
# Claude can now see: sql_query, calculator, data_summary
```

### 3. Tool Execution

When Claude calls a tool:

```python
tool_result = tool_registry.execute_tool(tool_name, **tool_input)

if tool_result.success:
    # Format and return results
else:
    # Handle error
```

---

## Creating Custom Tools

### Step 1: Create Tool Class

```python
from app.services.tool_executor import Tool, ToolResult
from typing import Dict, Any

class WeatherTool(Tool):
    """Tool for getting weather information."""

    def __init__(self, weather_api_key: str):
        super().__init__(
            name="get_weather",
            description="Get current weather for a location"
        )
        self.api_key = weather_api_key

    def get_schema(self) -> Dict[str, Any]:
        """Define tool schema for Claude."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name or coordinates"
                    },
                    "units": {
                        "type": "string",
                        "description": "Temperature units (celsius or fahrenheit)",
                        "default": "celsius"
                    }
                },
                "required": ["location"]
            }
        }

    def execute(self, **kwargs) -> ToolResult:
        """Execute weather lookup."""
        try:
            location = kwargs.get("location")
            units = kwargs.get("units", "celsius")

            # Call weather API
            weather_data = call_weather_api(location, units, self.api_key)

            return ToolResult(
                success=True,
                data={
                    "temperature": weather_data["temp"],
                    "condition": weather_data["condition"],
                    "location": location
                },
                metadata={"units": units}
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to get weather: {str(e)}"
            )
```

### Step 2: Register Tool

In `apps/api/app/services/chat.py`:

```python
# Add to tool registration
tool_registry.register(WeatherTool(api_key=settings.WEATHER_API_KEY))
```

### Step 3: Format Results (Optional)

Add custom formatting in chat.py:

```python
elif tool_name == "get_weather":
    temp = tool_result.data.get("temperature")
    condition = tool_result.data.get("condition")
    location = tool_result.data.get("location")
    units = tool_result.metadata.get("units")
    response_text += f"**Weather in {location}:**\n"
    response_text += f"- Temperature: {temp}°{'C' if units == 'celsius' else 'F'}\n"
    response_text += f"- Condition: {condition}\n"
```

---

## Testing Tools

### Manual Testing

Use the test scripts:

```bash
# Test all tools
/tmp/test_tools.sh

# Test specific tool
# Just ask Claude in the chat interface!
```

### Via Web UI

1. Go to http://localhost:8002
2. Login (test@example.com / password)
3. Navigate to "Ask AI"
4. Ask questions that would use different tools:
   - SQL: "What region has the highest revenue?"
   - Calculator: "Calculate 170000 * 1.15"
   - Summary: "Give me revenue statistics"

---

## Tool Safety

### Built-in Safety Features

1. **SQL Injection Prevention**
   - Blocks dangerous keywords (DROP, DELETE, INSERT, etc.)
   - Read-only queries
   - Row limits enforced

2. **Calculator Safety**
   - Only allows basic math operations
   - No access to Python builtins
   - Sandboxed eval()

3. **Error Handling**
   - All tools return standardized ToolResult
   - Errors gracefully handled and reported to user
   - No crashes from bad tool inputs

### Best Practices

- **Validate inputs**: Check parameters in execute()
- **Use try/except**: Always catch exceptions
- **Return ToolResult**: Use success=False for errors
- **Limit data size**: Don't return huge datasets
- **Add timeouts**: For external API calls
- **Log tool usage**: Track what tools are being called

---

## Architecture Benefits

### ✅ Extensibility
- Add new tools without changing core logic
- Tools are self-contained and testable
- Easy to enable/disable tools per tenant

### ✅ Safety
- Standardized error handling
- Tool validation before execution
- Sandboxed execution environments

### ✅ Discoverability
- Claude automatically sees all registered tools
- Tools self-document through schemas
- Consistent interface for all tools

### ✅ Flexibility
- Tools can be dataset-specific or global
- Support for async operations
- Easy to add authentication/authorization

---

## Next Steps

### Potential Tools to Add:

1. **API Call Tool** - Call external REST APIs
2. **Chart Generation Tool** - Create visualizations
3. **Data Export Tool** - Export results to CSV/Excel
4. **Email Tool** - Send reports via email
5. **Webhook Tool** - Trigger external webhooks
6. **ML Prediction Tool** - Run ML models on data
7. **Text Analysis Tool** - Sentiment analysis, summarization
8. **Time Series Tool** - Forecasting and trend analysis

### Advanced Features:

- **Tool Chaining**: One tool's output → another tool's input
- **Tool Permissions**: Control which users/tenants can use which tools
- **Tool Versioning**: Support multiple versions of tools
- **Tool Marketplace**: Allow users to install community tools
- **Tool Analytics**: Track tool usage and performance

---

## Files Modified

- ✅ `apps/api/app/services/tool_executor.py` - Tool framework with EntityExtractionTool and KnowledgeSearchTool
- ✅ `apps/api/app/services/chat.py` - Integrated tool registry
- ✅ `apps/api/app/services/llm.py` - Removed hardcoded SQL tool

---

## Testing Results

### Test 1: SQL Query Tool ✅
```
Q: "What is the average profit by customer segment?"
Result: Enterprise: $42,500 avg | Mid-Market: $26,000 | SMB: $16,000
```

### Test 2: Calculator Tool ✅
```
Q: "Calculate 170000 * 1.15"
Result: $195,500
```

### Test 3: Data Summary Tool ✅
```
Q: "Give me revenue statistics"
Result: Avg: $99,700 | Min: $45,000 | Max: $152,000
```

---

## Support

For issues or questions about the tool framework:
- Check tool execution logs in chat context
- Verify tool is registered in tool_registry
- Ensure tool schema matches Claude's requirements
- Test tool.execute() independently before integrating

**Happy tool building! 🛠️**
