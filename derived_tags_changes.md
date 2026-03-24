# Derived Tags Implementation

## Summary
The derived tags feature is fully implemented in the backend. This change adds the missing UI form so users can configure derived tags through the web interface.

## Architecture

### Backend (Already Existed)
- **`core/expression_engine.py`**: AST-based evaluator that safely converts JavaScript/Nashorn expressions to Python
  - Supports: arithmetic, comparisons, logical ops (`&&`, `||`), ternary operator, `Math.*` functions
  - Security: No `eval()`, no imports, whitelist of safe functions only
- **`core/models.py` → `DerivedTag`**: Pydantic model with `tag_id`, `expression_js`, `source_tag_ids`, `type`
- **`adapters/opcua_adapter.py` → `DerivedTagEvaluator`**: Caches source tag values, evaluates all derived tags after each scan cycle
  - Works in both real OPC-UA mode and simulation mode
  - Emits derived tag values as `DataEvent` objects to the EventBus

### Frontend (New)
- **`opcua_config.html`**: Added "Derived Tags" table per thing configuration
  - Columns: Tag ID, Expression (JS), Source Tag IDs (comma-separated), Type
  - "Add Derived Tag" button adds a new row
  - Expression input is a `<textarea>` with monospace font for code editing
  - Source Tag IDs field accepts comma-separated tag IDs
  - Type selector: number, string, boolean
- **`buildConfig()` JS function**: Updated to serialize `derived_tags` array into the config JSON
  - Each derived tag is serialized as: `{tag_id, expression_js, source_tag_ids: [...], type}`
  - Source IDs are split from comma-separated string into array

## Data Flow
```
User defines: derived tag "avg_temp" = "(temp1 + temp2) / 2" using source tags [temp1, temp2]
    ↓
Form → buildConfig() → JSON → POST /adapters/opcua/save
    ↓
OpcuaAdapterConfig validates via Pydantic
    ↓
OPCUAAdapter.run() creates DerivedTagEvaluator for each thing with derived_tags
    
On each scan: source tag values cached → DerivedTagEvaluator.evaluate_all()
    ↓
Results emitted as DataEvent(node_id="derived:avg_temp", value=computed)
    ↓
EventBus → LocalStore → Cloud (same pipeline as regular tags)
```

## Supported Expression Syntax
| JS/Nashorn Expression | Python Equivalent |
|---|---|
| `(a + b) / 2` | Arithmetic |
| `a > 100 ? 1 : 0` | Ternary → `if/else` |
| `a && b` | `a and b` |
| `a \|\| b` | `a or b` |
| `a === b` | `a == b` |
| `a !== b` | `a != b` |
| `Math.abs(a)` | `abs(a)` |
| `Math.sqrt(a)` | `math.sqrt(a)` |
| `Math.max(a, b)` | `max(a, b)` |
| `Math.min(a, b)` | `min(a, b)` |
| `Math.round(a)` | `round(a)` |
