# Lockstep Architecture & Trust Model (V2 - Updated)

## Trust Model Overview

Lockstep is a trust-minimized system composed of three primary layers:

- **TEE (Enclave)** → trusted for private evaluation and holdout integrity  
- **Validators** → verify reproducibility of public evaluation  
- **Cryptography** → ensures integrity of commitments and receipts  

Lockstep does not eliminate trust. It constrains where trust lives and makes violations observable.

---

## Trust Boundaries

- **TEE**: trusted for private evaluation and holdout secrecy  
- **Validators**: verify deterministic reproducibility of public grading  
- **Dataset Authorities**: define datasets, regimes, and updates  
- **Cryptography**: ensures data and receipt integrity  

---

## Dataset Governance

Datasets and regime definitions are published by designated dataset authorities.

Lockstep verifies:
- dataset integrity (Merkle commitments)
- dataset versioning

Lockstep does NOT guarantee:
- correctness of data
- fairness of regime labeling

Dataset lifecycle considerations:
- versioned dataset releases
- explicit holdout segmentation
- potential holdout rotation policies

---

## Evaluation Model

Evaluation consists of two paths:

### Public Path
- fully reproducible
- validators can re-run grading
- used for consistency checks

### Private Holdout Path
- executed inside TEE
- not externally reproducible
- protects against simple overfitting

Tradeoff:
Rotating holdouts improve resistance to overfitting but reduce strict reproducibility across time.

---

## Receipt Guarantees

Lockstep receipts prove:

- a specific solution was evaluated
- using a specific dataset version
- with a specific grader
- under deterministic rules

Lockstep receipts do NOT prove:
- strategy quality
- future performance
- absence of overfitting

---

## Execution Binding

Execution requests reference:

- `solution_commitment_hash`
- `evaluator_id`
- `dataset_version`

This ensures the executed strategy matches the graded artifact.

---

## Sealed Execution

Strategies are executed without revealing internal logic.

Open questions / constraints:
- output leakage risk
- execution policy enforcement
- debugging limitations

Execution integrity is assumed under the execution environment (e.g. KeeperHub).

---

## Validator Role

Validators:

- re-run public evaluation
- verify deterministic outputs
- detect divergence from receipts

Validators do NOT verify:
- private holdout integrity
- enclave correctness
- dataset correctness

Validators act as **consistency auditors**, not full trust anchors.

---

## Non-Guarantees

Lockstep does NOT guarantee:

- strategy profitability  
- absence of overfitting  
- correctness of dataset labeling  
- integrity of enclave hardware  

---

## Summary

Lockstep provides:

- constrained evaluation
- reproducible public grading
- private holdout protection
- verifiable execution binding

It is a system for making evaluation harder to manipulate, not a system for certifying truth.
