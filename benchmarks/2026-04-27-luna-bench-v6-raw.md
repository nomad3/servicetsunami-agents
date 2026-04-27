# Luna latency benchmark — 2026-04-27

- base_url: `http://localhost:8000`
- tenant: `09f9f6f0-0c12-40fb-9fef-b5eb1cd9ad68`
- runs/cell: 2 (after 1 warmup)
- started: 2026-04-27T14:10:23.996366+00:00
- finished: 2026-04-27T14:15:41.330477+00:00

**v0 caveat:** Phase A per-stage instrumentation (recall_ms / cli_spawn_ms / cli_first_byte_ms / post_dispatch_ms) is not in yet. Numbers below are **end-to-end wall time** of `POST /messages/enhanced` — what the user feels.

## Summary by prompt class

| cell | n | n_fail | wall p50 | wall p95 | wall avg | server p50 | platforms |
|---|---|---|---|---|---|---|---|
| greeting | 2 | 0 | 84 ms | 89 ms | 86 ms | 0 ms | template |
| light_recall | 2 | 0 | 16057 ms | 20294 ms | 18175 ms | 0 ms | local_gemma_tools |
| entity_recall | 2 | 0 | 20337 ms | 20655 ms | 20496 ms | 0 ms | local_gemma_tools |
| tool_read | 2 | 0 | 14986 ms | 17723 ms | 16354 ms | 0 ms | local_gemma_tools |
| multi_step | 2 | 0 | 17211 ms | 19889 ms | 18550 ms | 0 ms | local_gemma_tools |

## Stage breakdown (avg ms)

| cell | cli_credentials_missing | local_llm_ms | local_overhead_ms | local_rounds | local_tool_agent | local_tool_ms | local_total_ms | setup | template_match_ms |
|---|---|---|---|---|---|---|---|---|---|
| greeting | — | — | — | — | — | — | — | — | 0 |
| light_recall | 2 | 18022 | 0 | 1 | 18023 | 0 | 18022 | 0 | — |
| entity_recall | 1 | 20284 | 0 | 1 | 20285 | 0 | 20284 | 0 | — |
| tool_read | 1 | 16222 | 0 | 1 | 16223 | 0 | 16222 | 0 | — |
| multi_step | 1 | 18373 | 0 | 1 | 18373 | 0 | 18373 | 0 | — |

## All rows

| cell | run | cold | wall | server | tokens | platform | ok | error / preview |
|---|---|---|---|---|---|---|---|---|
| greeting | 1 | Y | 89 ms | — ms | — | template | ✅ | ¡Hola! Soy Luna. ¿En qué te puedo ayudar? |
| greeting | 2 | N | 84 ms | — ms | — | template | ✅ | ¡Hola! Soy Luna. ¿En qué te puedo ayudar? |
| light_recall | 1 | Y | 16057 ms | — ms | — | local_gemma_tools | ✅ | No veo un tema específico en nuestras últimas interacciones más allá de esta pregunta.  Para poder recordarte lo que me  |
| light_recall | 2 | N | 20294 ms | — ms | — | local_gemma_tools | ✅ | Para recordarte lo que hablamos, ¿podrías darme un poco más de contexto? ¿Estábamos hablando sobre un proyecto específic |
| entity_recall | 1 | Y | 20337 ms | — ms | — | local_gemma_tools | ✅ | Como tu copiloto de negocios, mi conocimiento sobre tu empresa está directamente ligado a la información que compartimos |
| entity_recall | 2 | N | 20655 ms | — ms | — | local_gemma_tools | ✅ | Para darte una respuesta precisa, necesito saber a qué te refieres con "mi negocio". Soy tu copiloto y asistente de IA,  |
| tool_read | 1 | Y | 14986 ms | — ms | — | local_gemma_tools | ✅ | I don't have a specific tool to list your recent workflows directly. My current tools are designed for searching and fin |
| tool_read | 2 | N | 17723 ms | — ms | — | local_gemma_tools | ✅ | I'm sorry, but I do not have a function available to list your recent workflows. My current tools are focused on searchi |
| multi_step | 1 | Y | 17211 ms | — ms | — | local_gemma_tools | ✅ | Para darte un resumen preciso, necesito saber a qué te refieres con "hoy". ¿Te refieres a:  1.  **Nuestras reuniones/pen |
| multi_step | 2 | N | 19889 ms | — ms | — | local_gemma_tools | ✅ | Para darte un resumen rápido, necesito saber a qué área te refieres. ¿Buscas un resumen de:  1.  Nuestras actividades de |
