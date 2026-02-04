# План следующих шагов

## Priority 1: Function/Module Metrics (HIGH)

**Цель:** Расширить метрики за пределы классов

### Шаг 1.1: Метрики функций
**Файлы:** `architecture.py`, `tools/arch.py`, `tools/schemas.py`

```python
# Метрики для функций:
- lines: количество строк
- parameters: количество параметров
- local_variables: локальные переменные
- calls_count: сколько функций вызывает
- called_by_count: сколько раз вызывается
- return_points: количество return statements
- nesting_depth: максимальная глубина вложенности
```

**Новые инструменты:**
- `lens_function_metrics` — метрики одной функции
- `lens_complex_functions` — топ N сложных функций

### Шаг 1.2: Метрики модулей
```python
# Метрики для модулей:
- functions: количество функций
- classes: количество классов
- imports: количество импортов
- exports: публичный API (__all__ или без _)
- lines: общее количество строк
- cohesion: связность внутри модуля
```

**Новые инструменты:**
- `lens_module_metrics` — метрики одного модуля
- `lens_largest_modules` — топ N модулей по размеру

### Шаг 1.3: Обновить compute_all_metrics()
Добавить в `architecture.py`:
```python
def compute_all_metrics(nodes, edges):
    # Существующий код для классов...

    # Добавить: метрики функций
    for node in nodes:
        if node.type == NodeType.FUNCTION:
            node_metrics[node.id] = compute_function_metrics(node, edges)

    # Добавить: метрики модулей
    for node in nodes:
        if node.type == NodeType.MODULE:
            node_metrics[node.id] = compute_module_metrics(node, nodes, edges)
```

---

## Priority 2: Large File Handling (MEDIUM)

**Цель:** Уменьшить потребление токенов при работе с большими функциями

### Шаг 2.1: Signature-only режим
**Файл:** `tools/navigation.py`

```python
def handle_get_node(params, ctx):
    node_id = params.get("node_id")
    signature_only = params.get("signature_only", False)

    if signature_only:
        # Вернуть только: def foo(a, b, c) -> int:
        return extract_signature(node.source)
```

**Обновить схему:**
```python
{
    "name": "lens_get_node",
    "input_schema": {
        "properties": {
            "node_id": {...},
            "signature_only": {
                "type": "boolean",
                "description": "Return only function signature, not body. Useful for large functions."
            }
        }
    }
}
```

### Шаг 2.2: Truncation с контекстом
```python
def handle_get_node(params, ctx):
    max_lines = params.get("max_lines", None)

    if max_lines and len(source_lines) > max_lines:
        return {
            "source": "\n".join(source_lines[:max_lines]),
            "truncated": True,
            "total_lines": len(source_lines),
            "shown_lines": max_lines
        }
```

### Шаг 2.3: Умный контекст
**Файл:** `tools/navigation.py`

```python
def handle_context(params, ctx):
    include_source = params.get("include_source", True)
    summary_mode = params.get("summary_mode", False)

    if summary_mode:
        # Для callers/callees вернуть только сигнатуры
        # Полный код только для целевой ноды
```

---

## Priority 3: Runtime Understanding (HIGH)

**Цель:** Улучшить понимание динамического кода

### Шаг 3.1: Паттерны getattr
**Файл:** `parsers/python.py`

```python
# Распознавать:
getattr(obj, "method_name")  # → добавить edge к obj.method_name
getattr(obj, f"get_{name}")  # → пометить как partial_match

# Новый тип edge:
EdgeType.DYNAMIC_CALL  # confidence: low
```

### Шаг 3.2: Анализ декораторов
```python
# Распознавать:
@app.route("/api")      # → entry_point: True
@property              # → getter/setter pattern
@classmethod           # → class-level method
@abstractmethod        # → interface pattern

# Добавить в Node:
decorators: list[str]
is_entry_point: bool
```

### Шаг 3.3: Factory паттерны
```python
# Распознавать:
def create_handler(type):
    return HANDLERS[type]()  # → partial edge to HANDLERS values

# Словари как dispatch tables:
HANDLERS = {
    "a": HandlerA,
    "b": HandlerB,
}
```

### Шаг 3.4: Confidence levels
**Файл:** `models.py`

```python
class EdgeConfidence(Enum):
    HIGH = "high"      # Static call: foo.bar()
    MEDIUM = "medium"  # Import alias: from x import y as z
    LOW = "low"        # Dynamic: getattr, HANDLERS[key]
    UNRESOLVED = "unresolved"  # eval, exec
```

---

## Priority 4: TypeScript Improvements (MEDIUM)

### Шаг 4.1: Лучше tsconfig paths
**Файл:** `parsers/config_reader.py` (новый)

```python
def read_tsconfig(project_root: Path) -> dict:
    """Read and merge tsconfig.json with extends."""
    # Поддержка extends
    # Раскрытие paths aliases
    # Обработка baseUrl
```

### Шаг 4.2: Monorepo imports
```python
# Распознавать:
from "@shared/utils" import foo  # → packages/shared/utils
from "../../packages/core" import bar
```

---

## Порядок реализации

```
Week 1: Function metrics (1.1)
        - compute_function_metrics()
        - lens_function_metrics
        - lens_complex_functions

Week 2: Module metrics (1.2)
        - compute_module_metrics()
        - lens_module_metrics
        - lens_largest_modules

Week 3: Signature-only mode (2.1, 2.2)
        - signature_only param
        - max_lines param
        - truncation handling

Week 4: Runtime patterns (3.1, 3.2)
        - getattr detection
        - decorator analysis
        - EdgeConfidence enum
```

---

## Файлы для изменения

| Приоритет | Файл | Изменения |
|-----------|------|-----------|
| 1.1 | architecture.py | compute_function_metrics() |
| 1.1 | tools/arch.py | handle_function_metrics(), handle_complex_functions() |
| 1.1 | tools/schemas.py | lens_function_metrics, lens_complex_functions |
| 1.2 | architecture.py | compute_module_metrics() |
| 1.2 | tools/arch.py | handle_module_metrics(), handle_largest_modules() |
| 2.1 | tools/navigation.py | signature_only в handle_get_node |
| 2.2 | tools/navigation.py | max_lines в handle_get_node |
| 3.1 | parsers/python.py | detect_dynamic_calls() |
| 3.2 | models.py | EdgeConfidence enum |
| 3.2 | parsers/python.py | extract_decorators() |

---

## Тестирование

Для каждого шага:
1. Unit тесты в `tests/test_architecture.py`
2. Integration тест на реальном проекте (Mosquito)
3. Проверка что 227 существующих тестов проходят
