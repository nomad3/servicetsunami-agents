"""
Temporal workflows for AgentProvision.

Workflow classes live in sibling modules (e.g. coalition_workflow,
learn_from_media_workflow). Runtime registration happens in
app/workers/orchestration_worker.py — add new workflows to its
`workflows=[...]` list and the related activities to `activities=[...]`.
"""
