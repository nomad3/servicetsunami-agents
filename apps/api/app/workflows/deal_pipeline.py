"""
Temporal workflow for full M&A deal pipeline orchestration via HCA REST API.

Steps:
1. discover - AI prospect discovery
2. score - Score each for sell-likelihood
3. research - Briefs for high-scorers
4. outreach - Create outreach drafts
5. advance - Move prospects through pipeline
6. sync - Sync to knowledge graph
"""

from temporalio import workflow
from datetime import timedelta
from typing import Dict, Any, List


@workflow.defn(sandboxed=False)
class DealPipelineWorkflow:
    """Durable workflow for full M&A deal pipeline orchestration.

    Steps:
    1. discover - AI prospect discovery
    2. score - Score each for sell-likelihood
    3. research - Briefs for high-scorers
    4. outreach - Create outreach drafts
    5. advance - Move prospects through pipeline
    6. sync - Sync to knowledge graph
    """

    @workflow.run
    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tenant_id = params["tenant_id"]
        industry = params["industry"]
        criteria = params.get("criteria", {})
        score_threshold = params.get("score_threshold", 70)
        outreach_type = params.get("outreach_type", "cold_email")

        retry_policy = workflow.RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
        )

        workflow.logger.info(f"Starting deal pipeline for {industry}")

        # Step 1: Discover
        discover_result = await workflow.execute_activity(
            "hca_discover_prospects",
            args=[tenant_id, industry, criteria],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )
        if discover_result.get("status") != "success":
            return {"status": "error", "step": "discover", "error": discover_result.get("error")}

        prospect_ids = discover_result.get("prospect_ids", [])
        workflow.logger.info(f"Discovered {len(prospect_ids)} prospects")
        if not prospect_ids:
            return {"status": "completed", "prospects_found": 0}

        # Step 2: Score
        score_results = await workflow.execute_activity(
            "hca_score_prospects",
            args=[tenant_id, prospect_ids],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )
        high_scorers = [
            p for p in score_results.get("results", [])
            if p.get("score", 0) >= score_threshold
        ]
        high_scorer_ids = [str(p["prospect_id"]) for p in high_scorers]
        workflow.logger.info(f"{len(high_scorer_ids)} above threshold ({score_threshold})")
        if not high_scorer_ids:
            return {"status": "completed", "prospects_found": len(prospect_ids), "above_threshold": 0}

        # Step 3: Research
        research_result = await workflow.execute_activity(
            "hca_generate_research",
            args=[tenant_id, high_scorer_ids],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )

        # Step 4: Outreach
        outreach_result = await workflow.execute_activity(
            "hca_generate_outreach",
            args=[tenant_id, high_scorer_ids, outreach_type],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        # Step 5: Advance pipeline
        await workflow.execute_activity(
            "hca_advance_pipeline",
            args=[tenant_id, high_scorer_ids, "contacted"],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy,
        )

        # Step 6: Sync to KG
        sync_result = await workflow.execute_activity(
            "hca_sync_knowledge_graph",
            args=[tenant_id, prospect_ids],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        return {
            "status": "completed",
            "prospects_found": len(prospect_ids),
            "above_threshold": len(high_scorer_ids),
            "research_generated": research_result.get("count", 0),
            "outreach_generated": outreach_result.get("count", 0),
            "synced_to_kg": sync_result.get("count", 0),
        }
