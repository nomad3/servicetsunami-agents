# Luna latency benchmark — 2026-04-27

- base_url: `http://localhost:8000`
- tenant: `09f9f6f0-0c12-40fb-9fef-b5eb1cd9ad68`
- runs/cell: 2 (after 1 warmup)
- started: 2026-04-27T14:30:16.770060+00:00
- finished: 2026-04-27T14:35:27.895487+00:00

**v0 caveat:** Phase A per-stage instrumentation (recall_ms / cli_spawn_ms / cli_first_byte_ms / post_dispatch_ms) is not in yet. Numbers below are **end-to-end wall time** of `POST /messages/enhanced` — what the user feels.

## Summary by prompt class

| cell | n | n_fail | wall p50 | wall p95 | wall avg | server p50 | platforms |
|---|---|---|---|---|---|---|---|
| greeting | 2 | 0 | 55 ms | 82 ms | 68 ms | 0 ms | template |
| light_recall | 2 | 0 | 15616 ms | 20814 ms | 18215 ms | 0 ms | local_gemma_tools |
| entity_recall | 2 | 0 | 30814 ms | 34660 ms | 32737 ms | 0 ms | local_gemma_tools |
| tool_read | 2 | 0 | 15260 ms | 20010 ms | 17635 ms | 0 ms | local_gemma_tools |
| multi_step | 2 | 0 | 17164 ms | 17852 ms | 17508 ms | 0 ms | local_gemma_tools |

## Stage breakdown (avg ms)

| cell | cli_credentials_missing | local_llm_ms | local_overhead_ms | local_rounds | local_tool_agent | local_tool_ms | local_total_ms | setup | template_match_ms |
|---|---|---|---|---|---|---|---|---|---|
| greeting | — | — | — | — | — | — | — | — | 0 |
| light_recall | 1 | 18072 | 0 | 1 | 18073 | 0 | 18072 | 0 | — |
| entity_recall | 1 | 32485 | 6 | 2 | 32532 | 39 | 32531 | 0 | — |
| tool_read | 1 | 17460 | 0 | 1 | 17460 | 0 | 17460 | 0 | — |
| multi_step | 1 | 17349 | 0 | 1 | 17350 | 0 | 17349 | 0 | — |

## All rows

| cell | run | cold | wall | server | tokens | platform | ok | error / preview |
|---|---|---|---|---|---|---|---|---|
| greeting | 1 | Y | 55 ms | — ms | — | template | ✅ | ¡Hola! Soy Luna. ¿En qué te puedo ayudar? |
| greeting | 2 | N | 82 ms | — ms | — | template | ✅ | ¡Hola! Soy Luna. ¿En qué te puedo ayudar? |
| light_recall | 1 | Y | 15616 ms | — ms | — | local_gemma_tools | ✅ | En nuestra última interacción, me preguntaste: "¿qué te dije la última vez?".  ¿Te refieres a un tema específico que dis |
| light_recall | 2 | N | 20814 ms | — ms | — | local_gemma_tools | ✅ | No tengo acceso a un historial de conversaciones más allá de lo que está en nuestra sesión actual. ¿Podrías recordarme e |
| entity_recall | 1 | Y | 34660 ms | — ms | — | local_gemma_tools | ✅ | Según la búsqueda, "mi negocio" ha sido identificado como un concepto general que se refiere a tu propio negocio, sirvie |
| entity_recall | 2 | N | 30814 ms | — ms | — | local_gemma_tools | ✅ | Según la conocimiento grafo, registro "mi negocio" como un concepto relacionado con su propia empresa.  Sin embargo, el  |
| tool_read | 1 | Y | 20010 ms | — ms | — | local_gemma_tools | ✅ | Para poder ayudarte a listar tus flujos de trabajo recientes, ¿podrías especificar a qué te refieres exactamente?  Mis h |
| tool_read | 2 | N | 15260 ms | — ms | — | local_gemma_tools | ✅ | I apologize, but I don't have a specific tool or function available to list your recent workflows. My current capabiliti |
| multi_step | 1 | Y | 17852 ms | — ms | — | local_gemma_tools | ✅ | Para darte un resumen rápido, necesito un poco de contexto. ¿Podrías especificar sobre qué área quieres el resumen? Por  |
| multi_step | 2 | N | 17164 ms | — ms | — | local_gemma_tools | ✅ | Para darte un resumen, necesito saber a qué te refieres con "hoy". ¿Te refieres a:  1.  Nuestras reuniones de hoy? 2.  L |
