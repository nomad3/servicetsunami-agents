# Luna latency benchmark — 2026-04-27

- base_url: `http://localhost:8000`
- tenant: `09f9f6f0-0c12-40fb-9fef-b5eb1cd9ad68`
- runs/cell: 2 (after 1 warmup)
- started: 2026-04-27T14:22:47.104337+00:00
- finished: 2026-04-27T14:27:33.145788+00:00

**v0 caveat:** Phase A per-stage instrumentation (recall_ms / cli_spawn_ms / cli_first_byte_ms / post_dispatch_ms) is not in yet. Numbers below are **end-to-end wall time** of `POST /messages/enhanced` — what the user feels.

## Summary by prompt class

| cell | n | n_fail | wall p50 | wall p95 | wall avg | server p50 | platforms |
|---|---|---|---|---|---|---|---|
| greeting | 2 | 0 | 41 ms | 56 ms | 48 ms | 0 ms | template |
| light_recall | 2 | 0 | 19465 ms | 22006 ms | 20735 ms | 0 ms | local_gemma_tools |
| entity_recall | 2 | 0 | 34764 ms | 38149 ms | 36456 ms | 0 ms | local_gemma_tools |
| tool_read | 2 | 0 | 15938 ms | 20454 ms | 18196 ms | 0 ms | local_gemma_tools |
| multi_step | 2 | 0 | 16818 ms | 20635 ms | 18726 ms | 0 ms | local_gemma_tools |

## Stage breakdown (avg ms)

| cell | cli_credentials_missing | local_llm_ms | local_overhead_ms | local_rounds | local_tool_agent | local_tool_ms | local_total_ms | setup | template_match_ms |
|---|---|---|---|---|---|---|---|---|---|
| greeting | — | — | — | — | — | — | — | — | 0 |
| light_recall | 1 | 20543 | 0 | 1 | 20544 | 0 | 20543 | 0 | — |
| entity_recall | 1 | 36216 | 4 | 2 | 36261 | 39 | 36260 | 0 | — |
| tool_read | 1 | 18037 | 0 | 1 | 18037 | 0 | 18037 | 0 | — |
| multi_step | 1 | 18538 | 0 | 1 | 18538 | 0 | 18538 | 0 | — |

## All rows

| cell | run | cold | wall | server | tokens | platform | ok | error / preview |
|---|---|---|---|---|---|---|---|---|
| greeting | 1 | Y | 41 ms | — ms | — | template | ✅ | ¡Hola! Soy Luna. ¿En qué te puedo ayudar? |
| greeting | 2 | N | 56 ms | — ms | — | template | ✅ | ¡Hola! Soy Luna. ¿En qué te puedo ayudar? |
| light_recall | 1 | Y | 19465 ms | — ms | — | local_gemma_tools | ✅ | Disculpa, el contexto de nuestra conversación previa es muy breve. ¿Podrías recordarme el tema o la información específi |
| light_recall | 2 | N | 22006 ms | — ms | — | local_gemma_tools | ✅ | Hasta ahora, solo hemos estado discutiendo qué me dijiste la última vez. Para poder recordártelo, ¿podrías darme un poco |
| entity_recall | 1 | Y | 34764 ms | — ms | — | local_gemma_tools | ✅ | Según la búsqueda, "mi negocio" aparece registrado como un concepto general relacionado contigo.  Para darte información |
| entity_recall | 2 | N | 38149 ms | — ms | — | local_gemma_tools | ✅ | Según lo que he podido buscar, "mi negocio" está registrado como un **concepto** general que se refiere a tu propio nego |
| tool_read | 1 | Y | 15938 ms | — ms | — | local_gemma_tools | ✅ | I don't have a specific function built-in right now to list your recent workflows. Are these workflows related to a spec |
| tool_read | 2 | N | 20454 ms | — ms | — | local_gemma_tools | ✅ | I don't have direct access to a log of "workflows" in the way a system execution history might track them. My current kn |
| multi_step | 1 | Y | 16818 ms | — ms | — | local_gemma_tools | ✅ | Para darte un resumen rápido, ¿podrías especificar a qué te refieres con "hoy"?  ¿Te gustaría un resumen de:  1.  **Nues |
| multi_step | 2 | N | 20635 ms | — ms | — | local_gemma_tools | ✅ | Para darte un resumen rápido, necesito saber de qué área quieres el resumen. "Hoy" puede abarcar muchas cosas.  ¿Te refi |
