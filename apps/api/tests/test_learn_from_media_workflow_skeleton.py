"""T1.3 skeleton tests for LearnFromMediaWorkflow + activities.

Bodies are NotImplementedError stubs; T3.1 fills activities, T3.2 fills
the workflow orchestration branches. These tests only assert the module
+ symbol registration so later tasks have a stable import surface.
"""


def test_workflow_registered():
    from app.workflows import learn_from_media_workflow as w
    assert hasattr(w, "LearnFromMediaWorkflow")


def test_activities_registered():
    from app.workflows.activities import learn_from_media_activities as a
    expected = {
        "act_extract_media", "act_transcribe_url",
        "act_synthesize_skill_draft", "act_dispatch_skill_review",
        "act_run_synthetic_test", "act_install_skill",
        "act_diffuse_learning",
    }
    actual = {n for n in dir(a) if n.startswith("act_")}
    assert expected.issubset(actual)
