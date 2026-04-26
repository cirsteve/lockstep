# Lockstep Architecture (V2 - Updated)

## Overview

Lockstep is a system for verifiable evaluation of private computation.

It separates:
- **solution development**
- **evaluation**
- **execution**

while binding them together through cryptographic commitments and receipts.

---

## Core Components and Trust Roles

- **Grader (TEE)**  
  Executes evaluation, including private holdout. Trusted for execution integrity and holdout secrecy.

- **Validators**  
  Re-run public evaluation to verify deterministic reproducibility. Act as consistency auditors.

- **Dataset Authority**  
  Publishes datasets, regime definitions, and holdout partitions. Responsible for data correctness.

- **Execution Layer (e.g. KeeperHub)**  
  Executes strategies bound to evaluated artifacts. Responsible for execution integrity.

- **Cryptographic Layer**  
  Provides commitments, hashing, and receipt integrity.

---

## Data Model

### Solution

- `solution_commitment_hash` → canonical commitment to solution (plaintext-derived)
- `encrypted_bundle_hash` → integrity of encrypted storage artifact

### Dataset

- `dataset_version`
- `dataset_root` (Merkle commitment)
- includes sealed holdout partition

### Evaluator

- `evaluator_id`
- content-addressed grader definition
- deterministic evaluation rules

### Receipt

Receipt binds:

- solution_commitment_hash
- dataset_version
- evaluator_id
- evaluation outputs
- timestamp
- TEE attestation

Receipt ID is derived from canonical serialized payload.

---

## Evaluation Flow

```
Solution →
  → Public Dataset → Deterministic Grading → Public Metrics
  → Private Holdout (TEE) → Signed Metrics

→ Receipt Generated
```

### Public Path
- fully reproducible
- validators can re-run grading
- used for consistency verification

### Private Path
- executed inside TEE
- not externally reproducible
- protects against simple overfitting

---

## Execution Flow

```
Allocator →
  authorizeUsage(solution_commitment_hash)

→ Execution Layer →
  loads encrypted strategy
  executes signals
  produces outcomes
```

### Execution Binding

Execution MUST reference:

- `solution_commitment_hash`
- `evaluator_id`
- `dataset_version`

This ensures the executed strategy matches the graded artifact.

---

## Validator Flow

```
Validator →
  fetch receipt
  fetch dataset (public portion)
  re-run grader

→ compare outputs
→ accept or challenge
```

Validators verify:
- deterministic grading
- dataset consistency
- receipt reproducibility (public path)

Validators do NOT verify:
- private holdout
- enclave correctness
- dataset correctness

---

## Dataset Lifecycle

Datasets are:

- versioned
- Merkle committed
- published by dataset authorities
- include explicit holdout partition

Updates:
- produce new dataset_version
- do not invalidate prior receipts
- may include updated regimes or data corrections

Tradeoff:
- static datasets improve reproducibility
- rotating datasets reduce overfitting

---

## Sealed Execution

Strategies remain encrypted end-to-end.

Execution layer:
- decrypts within controlled environment
- runs strategy logic
- outputs signals or trades

Open constraints:
- output leakage risk
- limited debugging visibility
- enforcement of execution policies

---

## Guarantees vs Non-Guarantees

### Guarantees

- evaluation consistency (public path)
- receipt integrity
- binding between evaluation and execution
- reproducible grading

### Non-Guarantees

Lockstep does NOT guarantee:

- strategy profitability
- absence of overfitting
- correctness of dataset labeling
- integrity of enclave hardware

---

## Summary

Lockstep enforces:

- constrained evaluation
- reproducible verification
- private holdout protection
- execution binding to evaluated artifacts

It is not a system for proving truth.

It is a system for making evaluation harder to manipulate and easier to audit.
