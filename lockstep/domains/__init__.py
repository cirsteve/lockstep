"""Layer 3 — concrete domain instantiations.

Each subpackage provides the four pieces a domain needs:
    solution.py    — concrete SolutionPayload subclass
    dataset.py     — concrete DatasetPayload subclass
    grader.py      — concrete Grader subclass
    evaluation.py  — Evaluation subclass + registered Evaluator

Adding a new domain must require zero changes to Layers 1 or 2.
"""
