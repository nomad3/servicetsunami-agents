# Luna latency benchmark — 2026-04-27

- base_url: `http://localhost:8000`
- tenant: `09f9f6f0-0c12-40fb-9fef-b5eb1cd9ad68`
- runs/cell: 2 (after 1 warmup)
- started: 2026-04-27T13:06:05.133327+00:00
- finished: 2026-04-27T13:14:42.239596+00:00

**v0 caveat:** Phase A per-stage instrumentation (recall_ms / cli_spawn_ms / cli_first_byte_ms / post_dispatch_ms) is not in yet. Numbers below are **end-to-end wall time** of `POST /messages/enhanced` — what the user feels.

## Summary by prompt class

| cell | n | n_fail | wall p50 | wall p95 | wall avg | server p50 | platforms |
|---|---|---|---|---|---|---|---|
| greeting | 2 | 0 | 28044 ms | 32644 ms | 30344 ms | 0 ms | local_gemma_tools |
| light_recall | 2 | 0 | 34837 ms | 36308 ms | 35572 ms | 0 ms | local_gemma_tools |
| entity_recall | 2 | 0 | 36742 ms | 39011 ms | 37876 ms | 0 ms | local_gemma_tools |
| tool_read | 2 | 0 | 32161 ms | 35115 ms | 33638 ms | 0 ms | local_gemma_tools |
| multi_step | 2 | 0 | 34143 ms | 41529 ms | 37836 ms | 0 ms | local_gemma_tools |

## All rows

| cell | run | cold | wall | server | tokens | platform | ok | error / preview |
|---|---|---|---|---|---|---|---|---|
| greeting | 1 | Y | 28044 ms | — ms | — | local_gemma_tools | ✅ | Hola! I'm here and ready to help coordinate everything with AgentProvision.  What can I assist you with today? Are we re |
| greeting | 2 | N | 32644 ms | — ms | — | local_gemma_tools | ✅ | Hola. I'm here and ready to assist you!  Is there anything specific I can help you with today? Whether it's reviewing do |
| light_recall | 1 | Y | 36308 ms | — ms | — | local_gemma_tools | ✅ | I'm sorry, but the search tool did not return any specific content regarding what you mentioned last.  Could you perhaps |
| light_recall | 2 | N | 34837 ms | — ms | — | local_gemma_tools | ✅ | No tengo registro específico de lo que nos dijimos la última vez con la búsqueda de memoria. El resultado de la consulta |
| entity_recall | 1 | Y | 39011 ms | — ms | — | local_gemma_tools | ✅ | I checked both my general entity database and our conversation knowledge base for information regarding "your business," |
| entity_recall | 2 | N | 36742 ms | — ms | — | local_gemma_tools | ✅ | Based on the memory and knowledge search, I don't have any specific details regarding "your business" at this moment.  T |
| tool_read | 1 | Y | 32161 ms | — ms | — | local_gemma_tools | ✅ | Based on the memory checks I just performed, I couldn't retrieve a list of your recent workflows.  I can check for speci |
| tool_read | 2 | N | 35115 ms | — ms | — | local_gemma_tools | ✅ | I attempted to retrieve your recent workflows using our knowledge base, but I couldn't find a specific list or history a |
| multi_step | 1 | Y | 41529 ms | — ms | — | local_gemma_tools | ✅ | He realizado una revisión de mi memoria y de la base de conocimientos sobre eventos de hoy, pero no he podido recuperar  |
| multi_step | 2 | N | 34143 ms | — ms | — | local_gemma_tools | ✅ | No he encontrado un resumen específico de lo que pasó hoy en mi memoria o en los registros recientes.  Para poder darte  |
