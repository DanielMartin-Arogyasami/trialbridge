"""TrialBridge-Bench build pipeline.

Pipeline order:
    pull_trials -> build_criteria -> patients -> autolabel -> annotate
                -> build_dataset -> run_eval -> metrics -> make_tables
"""

__version__ = "0.1.0"
